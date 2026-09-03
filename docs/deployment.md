# Deployment — Google Cloud Run

**Status:** originally the step-by-step guide for the *first* real deploy of the `backend/` Django project (all modules — currently just `admissions`) to Cloud Run under `admissions.tcsch.edu.gh`, written 2026-08-19. **That first deploy has since happened, and the service has been redeployed many times** (latest revision `admissions-00019-qm7`, 2026-09-03). This doc is now two things at once: (1) an accurate record of the GCP-side setup that exists, and (2) a from-scratch runbook still valid for standing the whole thing up again elsewhere. Treat the per-step "not run" / "written for you" phrasing as historical unless a step is called out as outstanding in **Current deployment state** below.

Read `shared-stack.md` first for the overall architecture this assumes (Supabase Postgres, Secret Manager, Cloudflare in front).

---

## Current deployment state (last reconciled 2026-09-03)

**Live and working** (verified against `https://admissions.tcsch.edu.gh` and `gcloud`):

- Service `admissions` in `europe-west1`, project `tcs-os`, serving revision `admissions-00019-qm7`. Custom domain resolves and serves over HTTPS — steps 1–4, 7, 11, 12 are effectively done (the CNAME to `ghs.googlehosted.com` is in place and routing).
- Secret Manager holds `admissions-secret-key`, `admissions-database-url`, `admissions-supabase-key`, `admissions-resend-key`, `admissions-turnstile-secret`, `admissions-bulk-email-secret`. IAM bound to the `admissions-runner@tcs-os.iam.gserviceaccount.com` runtime SA (step 3).
- `admissions-migrate` Cloud Run Job exists and has been run through migration `0014` (step 5). `admissions-configure-storage` job pattern from step 5b has been run against the real bucket.
- `admissions-bulk-email` Cloud Tasks queue exists (`--max-dispatches-per-second=5`, `--max-attempts=3`) — step 5c done.
- Resend: **both** sending domains verified — `tcsch.edu.gh` (step 6) and `updates.tcsch.edu.gh` (step 6b), the latter confirmed `"status": "verified"` via a real `GET /domains` call on 2026-08-28. A real Phase 6 campaign has been sent in production.
- All step-7 env vars are set on the live service, including Phase 6's six (`GCP_PROJECT_ID` etc.) and the 2026-09-02 CORS pair (`CORS_ALLOWED_ORIGINS`, `CORS_ALLOWED_ORIGIN_REGEXES`).
- **Cloudflare Turnstile widget allowed-domains** — the "TCS OS" widget now allows `tcsch.edu.gh`, `www.tcsch.edu.gh`, and `tcsch.vercel.app` (the marketing-site preview host) alongside `admissions.tcsch.edu.gh`. Verified end to end on 2026-09-03 by a real production PDF-gate submission from the marketing site — Turnstile solved, the lead was captured, and the confirmation email (with the Admissions Overview & Fees PDF) actually landed. No longer a blocker for the Lead-capture widgets.

**Genuinely still outstanding:**

- **Step 13 — Cloudflare rate-limit rule on `/api/admissions/*`** — never applied. Dashboard config; needs the DNS record proxied (orange cloud) first.
- **Step 8 — staff admin login**: at least one superuser exists (real campaigns have been sent from admin), but whether it was created via the step-8 job or ad hoc isn't recorded here.
- **`admissions.can_send_bulk_email` / `admissions.can_view_health_info`** grants — still ungranted to any Group by design; a deliberate decision for whoever owns go-live.
- **Step 9 / step 10** verification checklists — the `*.run.app` direct-hit check and the production-origin Supabase upload check — worth running once as documented, not known to have been done formally.

---

## 0. Prerequisites

- `gcloud` CLI installed and authenticated: `gcloud auth login`
- A GCP project created and billing enabled. If you haven't made one yet:
  ```
  gcloud projects create tcs-os-admissions --name="TCS OS"
  gcloud config set project tcs-os-admissions
  gcloud billing projects link tcs-os-admissions --billing-account=YOUR_BILLING_ACCOUNT_ID
  ```
- Pick a region close to Ghana — Cloud Run has no Africa region; `europe-west1` (Belgium) is the closest reasonable option. All commands below use `REGION=europe-west1`; change it if you'd rather use something else.
- The real Supabase `DATABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and (once you have one) `RESEND_API_KEY` on hand — pull these from your local `backend/.env`, don't retype them from memory.

```
export PROJECT_ID=tcs-os-admissions
export REGION=europe-west1
gcloud config set project $PROJECT_ID
```

## 1. Enable required APIs

```
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  cloudtasks.googleapis.com
```

`cloudtasks.googleapis.com` is for Phase 6's bulk email background sends (see step 5c) — **already enabled** on the real project as part of that build, included here so a from-scratch setup elsewhere stays complete.

## 2. Artifact Registry — where the built image lives

```
gcloud artifacts repositories create tcs-os \
  --repository-format=docker \
  --location=$REGION \
  --description="TCS OS backend images"
```

## 3. Secrets in Secret Manager

Only things that are genuinely sensitive go here — `SECRET_KEY`, the database URL (has credentials embedded), the Supabase service-role key, the Resend key, the Turnstile secret key, and the bulk-email internal shared secret. Everything else (`ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `TURNSTILE_SITE_KEY` — public by design, like a Stripe publishable key — etc.) is plain config, not a secret, and gets set as a normal env var on the Cloud Run service in step 7 instead.

Generate a real `SECRET_KEY` first if you don't already have a production one — **do not reuse your local dev `.env`'s value**:
```
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Then create each secret (replace the placeholder values with your real ones — pull `DATABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from `backend/.env`):
```
echo -n "PASTE_THE_GENERATED_SECRET_KEY" | gcloud secrets create admissions-secret-key --data-file=-
echo -n "postgres://user:password@host:5432/postgres" | gcloud secrets create admissions-database-url --data-file=-
echo -n "PASTE_SUPABASE_SERVICE_ROLE_KEY" | gcloud secrets create admissions-supabase-key --data-file=-
echo -n "PASTE_RESEND_API_KEY" | gcloud secrets create admissions-resend-key --data-file=-
echo -n "PASTE_TURNSTILE_SECRET_KEY" | gcloud secrets create admissions-turnstile-key --data-file=-
python3 -c "import secrets; print(secrets.token_urlsafe(48))" | tr -d '\n' | gcloud secrets create admissions-bulk-email-secret --data-file=-
```

`PASTE_TURNSTILE_SECRET_KEY` comes from a real Cloudflare Turnstile widget, not the published test key the code defaults to for local dev (`1x0000000000000000000000000000000AA` — see `docs/admissions/02-stack-and-schema.md`). Create one at [the Cloudflare dashboard → Turnstile](https://dash.cloudflare.com) → Add widget, widget mode "Managed" — copy both the site key and secret key it gives you; the site key goes in step 7 as a plain env var (`TURNSTILE_SITE_KEY`), not a secret. On the real project this is the widget named **"TCS OS"**, and its allowed-domains list holds `admissions.tcsch.edu.gh` (admissions app) plus `tcsch.edu.gh`, `www.tcsch.edu.gh`, and `tcsch.vercel.app` (the separate marketing site's Lead-capture widgets) — one widget serves both. Add any further marketing-site preview hostnames to the same widget.

`admissions-bulk-email-secret` isn't from any external service — it's an arbitrary random value your own code both sets (on the Cloud Run service, step 7) and checks (in `BulkEmailBatchSendView`), so Cloud Tasks' dispatch to the internal batch-send endpoint can be told apart from a request from anyone else on the internet. **Already created on the real project** for this build — generating a fresh one here would just orphan the one Cloud Tasks tasks already reference, so only regenerate this if you specifically want to rotate it (and update the Cloud Run service to match in the same step).

Grant the Cloud Run runtime service account access to read them. By default Cloud Run uses the *Compute Engine default service account* unless you've created a dedicated one — check which one applies with `gcloud run services describe`, or create a dedicated one now (recommended, tighter scope than the default):
```
gcloud iam service-accounts create admissions-runner \
  --display-name="TCS OS Admissions Cloud Run runtime"

export RUNNER_SA=admissions-runner@$PROJECT_ID.iam.gserviceaccount.com

for SECRET in admissions-secret-key admissions-database-url admissions-supabase-key admissions-resend-key admissions-turnstile-key admissions-bulk-email-secret; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:$RUNNER_SA" \
    --role="roles/secretmanager.secretAccessor"
done

# Phase 6 — the runner service account also needs to *create* Cloud Tasks
# (to enqueue a bulk send), not just read secrets:
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$RUNNER_SA" \
  --role="roles/cloudtasks.enqueuer"
```

Both the secret and the two IAM grants above are **already done** on the real project as part of this build.

## 4. Build and push the image

Build context is `backend/` (the Dockerfile already assumes this — `WORKDIR /app` + `COPY . .` copies everything under `backend/`, including the public form templates and branding assets, which live inside `backend/` for exactly this reason):

```
cd backend
export IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/tcs-os/admissions-backend:latest

gcloud auth configure-docker $REGION-docker.pkg.dev

docker build -t $IMAGE .
docker push $IMAGE
```

(Or skip the local Docker daemon entirely and let Cloud Build do it: `gcloud builds submit --tag $IMAGE .` from inside `backend/`.)

## 5. Run database migrations (one-off, before first deploy serves traffic)

Migrations need the real `DATABASE_URL` but shouldn't run as part of every deploy — a Cloud Run **Job** using the same image is the standard way to run one-off management commands without exposing production credentials on your own machine:

```
gcloud run jobs create admissions-migrate \
  --image=$IMAGE \
  --region=$REGION \
  --service-account=$RUNNER_SA \
  --set-secrets="SECRET_KEY=admissions-secret-key:latest,DATABASE_URL=admissions-database-url:latest" \
  --command="python" \
  --args="manage.py,migrate,--noinput"

gcloud run jobs execute admissions-migrate --region=$REGION --wait
```

Re-run `gcloud run jobs execute admissions-migrate --region=$REGION --wait` after any future deploy that includes new migrations — it's not automatic.

### 5b. Configure the Storage bucket's upload limits (one-off, re-run if limits change)

Same Cloud Run Jobs pattern, this time calling `manage.py configure_storage_bucket` — it pushes `MAX_UPLOAD_SIZE_MB` and the allowed document MIME types (PDF/JPG/PNG) onto the `admissions-documents` bucket itself via the Supabase Storage API, so oversized/disallowed uploads are rejected server-side even if a client bypasses the app's own checks (see `docs/admissions/02-stack-and-schema.md` → "Upload constraints"). Needs the Supabase secrets, not the database ones:

```
gcloud run jobs create admissions-configure-storage \
  --image=$IMAGE \
  --region=$REGION \
  --service-account=$RUNNER_SA \
  --set-env-vars="SUPABASE_URL=https://your-project.supabase.co" \
  --set-env-vars="SUPABASE_STORAGE_BUCKET=admissions-documents" \
  --set-secrets="SECRET_KEY=admissions-secret-key:latest,DATABASE_URL=admissions-database-url:latest,SUPABASE_SERVICE_ROLE_KEY=admissions-supabase-key:latest" \
  --command="python" \
  --args="manage.py,configure_storage_bucket"

gcloud run jobs execute admissions-configure-storage --region=$REGION --wait
```

Re-run this any time `MAX_UPLOAD_SIZE_MB` or the allowed extensions change — it's not automatic and not part of the regular deploy. (If you set `MAX_UPLOAD_SIZE_MB` to something other than the 10MB default, add `--set-env-vars="MAX_UPLOAD_SIZE_MB=..."` here too.)

### 5c. Create the Cloud Tasks queue for bulk email (one-off)

Phase 6's background-job infrastructure — dispatches a bulk/marketing send in batches instead of one long synchronous request (see `docs/admissions/02-stack-and-schema.md` for why: Resend's 10 req/sec team-wide rate limit makes a per-recipient loop infeasible at TCS's real family count). Unlike the two Cloud Run Jobs above, this is a standing queue, not a one-shot job:

```
gcloud tasks queues create admissions-bulk-email \
  --location=$REGION \
  --max-dispatches-per-second=5 \
  --max-concurrent-dispatches=5 \
  --max-attempts=3
```

`--max-dispatches-per-second=5` is the real throttle — it keeps this queue safely under Resend's rate limit without any hand-rolled pacing logic in the app itself. `--max-attempts=3` must match `CLOUD_TASKS_MAX_ATTEMPTS` in step 7's env vars — the batch-send handler uses that number to know whether a failed batch still has retries coming (leave the recipients "pending") or not (mark them "failed" for good, see `admissions/views.py:BulkEmailBatchSendView`). **Already created** on the real project as part of this build, in `$REGION`.

## 6. Verify a sending domain in Resend

Emails will silently fail (or get rejected by Resend) until `tcsch.edu.gh` —
the domain in `DEFAULT_FROM_EMAIL` — is added and verified. `RESEND_API_KEY`
being set is not enough by itself; do this before step 7's deploy so it's
not forgotten once the service is live and staff are expecting real email.

1. In the Resend dashboard: **Domains → Add Domain**, enter `tcsch.edu.gh`
   (the same domain `admissions@tcsch.edu.gh` already uses — no need for a
   dedicated sending subdomain unless you specifically want to isolate
   sending reputation from the school's other mail).
2. Resend generates the exact records for this domain (an SPF TXT record,
   one or more DKIM TXT records, and a Return-Path MX record) on the
   domain's **Records** tab — always read them from there, they're
   generated per-domain, not a fixed value you can copy from this guide.
3. **If `tcsch.edu.gh` already has an SPF TXT record** — likely, if the
   school's existing email (Google Workspace, Microsoft 365, etc.) already
   sends from this domain — do not add a second one. A domain can only have
   one SPF record; two is a hard failure, not a warning. Merge Resend's
   `include:` mechanism into the existing record instead, e.g. `v=spf1
   include:_spf.google.com include:amazonses.com ~all` combining both.
   Check what's already there first: `dig TXT tcsch.edu.gh +short`.
4. Add the DKIM and MX records in Cloudflare like any other DNS record —
   TXT/MX records are never proxied regardless of the proxy toggle, so
   there's no grey/orange-cloud decision here (unlike the CNAME in step 12).
5. Back in the Resend dashboard, click **Verify DNS Records**. Propagation
   can take up to 24 hours; Resend re-checks and flips each record's status
   (missing → verified) as it resolves.

### 6b. Verify the bulk-email sending subdomain (Phase 6) — DONE 2026-08-28

**Status:** `updates.tcsch.edu.gh` was added and verified in Resend by the user; confirmed `"status": "verified"` via a real `GET /domains` API call on 2026-08-28 (see `docs/admissions/04-build-log.md`). The steps below are retained as the how-to for a from-scratch rebuild — they do **not** still need doing.

Unlike step 6 above, this one **is** the "isolate sending reputation" case that step 6 mentions in passing — bulk/marketing mail is far likelier to generate spam complaints or a high bounce rate than a one-off transactional confirmation, and a shared domain would let that damage the deliverability of the offer/confirmation emails that actually matter. `BULK_EMAIL_FROM_EMAIL` defaults to `updates@updates.tcsch.edu.gh` — a separate subdomain from `tcsch.edu.gh`, not just a separate local part.

1. In the Resend dashboard: **Domains → Add Domain**, enter `updates.tcsch.edu.gh` (a subdomain, added as its own domain in Resend — not a record under the existing `tcsch.edu.gh` entry from step 6).
2. Same as step 6: Resend generates its own SPF/DKIM/MX records on this new domain's **Records** tab. Since this is a dedicated subdomain with no existing mail flow of its own, there's no "merge with an existing SPF record" concern here — add Resend's records as given.
3. Add those records in Cloudflare DNS, same as step 6 (TXT/MX, never proxied).
4. Back in Resend, **Verify DNS Records**.
5. Once verified, either leave `BULK_EMAIL_FROM_EMAIL` at its default (`updates@updates.tcsch.edu.gh`) or set it to whatever local part you'd rather send from on that subdomain — set via step 7's env vars.

## 7. Deploy the Cloud Run service

Non-secret config as plain env vars, secrets pulled in via `--set-secrets`. `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`/`FRONTEND_BASE_URL` are set to the real domain here — that's the one place they actually get their production values, `settings.py` itself only has localhost dev defaults on purpose.

`CORS_ALLOWED_ORIGINS` / `CORS_ALLOWED_ORIGIN_REGEXES` are for the **separate marketing site** (`tcsch.edu.gh`) whose PDF-gate and quick-interest widgets POST here cross-origin — `settings.py` now defaults these to the production marketing origins plus `*.vercel.app` previews, so you only need the env vars below to *add* origins or override. A value with commas needs gcloud's alternate-delimiter form (`^##^KEY=a,b`), shown below:

```
gcloud run deploy admissions \
  --image=$IMAGE \
  --region=$REGION \
  --service-account=$RUNNER_SA \
  --allow-unauthenticated \
  --min-instances=0 \
  --set-env-vars="DEBUG=False" \
  --set-env-vars="ALLOWED_HOSTS=admissions.tcsch.edu.gh" \
  --set-env-vars="CSRF_TRUSTED_ORIGINS=https://admissions.tcsch.edu.gh" \
  --set-env-vars="^##^CORS_ALLOWED_ORIGINS=https://tcsch.edu.gh,https://www.tcsch.edu.gh" \
  --set-env-vars="CORS_ALLOWED_ORIGIN_REGEXES=^https://[a-z0-9-]+\.vercel\.app$" \
  --set-env-vars="FRONTEND_BASE_URL=https://admissions.tcsch.edu.gh" \
  --set-env-vars="DEFAULT_FROM_EMAIL=admissions@tcsch.edu.gh" \
  --set-env-vars="ADMISSIONS_STAFF_EMAIL=admissions@tcsch.edu.gh" \
  --set-env-vars="SUPABASE_URL=https://your-project.supabase.co" \
  --set-env-vars="SUPABASE_STORAGE_BUCKET=admissions-documents" \
  --set-env-vars="OFFER_EXPIRY_DAYS=14" \
  --set-env-vars="TURNSTILE_SITE_KEY=PASTE_REAL_TURNSTILE_SITE_KEY" \
  --set-env-vars="BULK_EMAIL_FROM_EMAIL=updates@updates.tcsch.edu.gh" \
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID" \
  --set-env-vars="CLOUD_TASKS_LOCATION=$REGION" \
  --set-env-vars="CLOUD_TASKS_QUEUE=admissions-bulk-email" \
  --set-env-vars="CLOUD_TASKS_MAX_ATTEMPTS=3" \
  --set-secrets="SECRET_KEY=admissions-secret-key:latest" \
  --set-secrets="DATABASE_URL=admissions-database-url:latest" \
  --set-secrets="SUPABASE_SERVICE_ROLE_KEY=admissions-supabase-key:latest" \
  --set-secrets="RESEND_API_KEY=admissions-resend-key:latest" \
  --set-secrets="TURNSTILE_SECRET_KEY=admissions-turnstile-key:latest" \
  --set-secrets="BULK_EMAIL_INTERNAL_SECRET=admissions-bulk-email-secret:latest"
```

If `TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` are left unset here, the app falls back to Cloudflare's published "always passes" test keys — the three public forms will work, but offer **no actual bot protection** in production. Don't skip this.

`GCP_PROJECT_ID`/`CLOUD_TASKS_LOCATION`/`CLOUD_TASKS_QUEUE`/`CLOUD_TASKS_MAX_ATTEMPTS` must match step 5c's real queue exactly, or Phase 6's "Send" action in admin will fail to enqueue (cleanly — see `admissions/admin.py:send_campaign`, which rolls back to Draft rather than leaving a stuck "Queued" campaign with nothing dispatched). `BULK_EMAIL_FROM_EMAIL`'s domain must be verified per step 6b before a real send will actually deliver.

`--allow-unauthenticated` is required — this serves public admissions forms, not an internal tool. `min-instances=0` keeps the scale-to-zero cost profile `shared-stack.md` calls for; add `--min-instances=1` later if cold starts on the inquiry form become a real complaint.

Note the `Service URL` printed at the end of this command (something like `https://admissions-xxxxxxxx-ew.a.run.app`) — you'll need it for step 9.

## 8. Create a staff admin login

Same Cloud Run Jobs pattern as migrations, using `createsuperuser --noinput`. The password goes through Secret Manager rather than a literal `--set-env-vars` value — typed directly on the command line it would sit in your shell history in plaintext, and would *also* persist in the job's own stored definition (`gcloud run jobs describe` shows plain `--set-env-vars` values to anyone with read access on the project; `--set-secrets` only stores a reference, never the value):

```
read -s -p "New admin password: " ADMIN_PASSWORD && echo
printf '%s' "$ADMIN_PASSWORD" | gcloud secrets create admissions-superuser-password --data-file=-
unset ADMIN_PASSWORD

gcloud secrets add-iam-policy-binding admissions-superuser-password \
  --member="serviceAccount:$RUNNER_SA" \
  --role="roles/secretmanager.secretAccessor"

gcloud run jobs create admissions-createsuperuser \
  --image=$IMAGE \
  --region=$REGION \
  --service-account=$RUNNER_SA \
  --set-secrets="SECRET_KEY=admissions-secret-key:latest,DATABASE_URL=admissions-database-url:latest,DJANGO_SUPERUSER_PASSWORD=admissions-superuser-password:latest" \
  --set-env-vars="DJANGO_SUPERUSER_USERNAME=admin,DJANGO_SUPERUSER_EMAIL=admissions@tcsch.edu.gh" \
  --command="python" \
  --args="manage.py,createsuperuser,--noinput"

gcloud run jobs execute admissions-createsuperuser --region=$REGION --wait
```

`read -s` reads the password interactively without echoing it, and — because it's typed as input rather than passed as part of the command line — it never lands in shell history either; only the `read -s -p "..."` command itself does, which contains no secret.

Clean up afterward so nothing lingers longer than it needs to:
```
gcloud run jobs delete admissions-createsuperuser --region=$REGION
gcloud secrets delete admissions-superuser-password
```
Change the password from inside `/admin/` once logged in, same as any first-login flow.

## 9. Verify before touching DNS

Hit the raw Cloud Run URL directly first — this confirms the service itself works before any DNS/Cloudflare layer is in the picture:
```
curl -I https://admissions-xxxxxxxx-ew.a.run.app/inquiry
curl -I https://admissions-xxxxxxxx-ew.a.run.app/admin/login/
```
Both should return `200`. If `ALLOWED_HOSTS` doesn't yet include the `*.run.app` hostname, this 400s — that's expected and fine (the deploy command above only allowlists the real domain); just confirm it fails with Django's own `DisallowedHost` message, not a container crash.

## 10. Confirm document uploads work from the production origin

Application document uploads (`POST /api/admissions/upload-url/` → signed Supabase Storage PUT) go directly from the parent's browser to Supabase, not through Django — worth confirming the production domain isn't blocked before calling this feature verified in production.

**There isn't a per-origin CORS allowlist to update here**, unlike Django's own `CORS_ALLOWED_ORIGINS`. Hosted Supabase Storage's API responds with permissive CORS headers (`Access-Control-Allow-Origin: *`) for every origin by default — there's no dashboard setting restricting it to `localhost`/`127.0.0.1:5500` from dev, so there's nothing to add for `https://admissions.tcsch.edu.gh` specifically. Access control for uploads is entirely the short-lived signed URL itself (already the design — the service-role key that mints it never leaves Django), not CORS.

Confirm that behavior directly rather than assuming it holds in production too:
```
curl -s -D - -o /dev/null -X OPTIONS \
  -H "Origin: https://admissions.tcsch.edu.gh" \
  -H "Access-Control-Request-Method: PUT" \
  "https://YOUR_PROJECT.supabase.co/storage/v1/object/admissions-documents/test" \
  | grep -i "access-control-allow-origin"
```
Expect `access-control-allow-origin: *` (or an echo of the `Origin` header) back. If Supabase has changed this default by the time you deploy, or your project has some other CORS restriction configured, this is where that would show up — the Storage section of the Supabase dashboard is the place to check next if it doesn't.

Then do one real end-to-end check through the actual production form: submit a test Application via `https://admissions.tcsch.edu.gh/apply` with a document attached, confirm it lands in the Supabase bucket, then delete the test row/file afterward.

## 11. Custom domain mapping

Point `admissions.tcsch.edu.gh` at this service via Cloud Run's own domain mapping (not a bare CNAME to the `*.run.app` hostname — Cloud Run's edge needs the domain formally mapped and verified to route it correctly and to issue a managed TLS cert for it):

```
gcloud run domain-mappings create \
  --service=admissions \
  --domain=admissions.tcsch.edu.gh \
  --region=$REGION
```

This prints the DNS record(s) to add — for a subdomain (not the bare apex `tcsch.edu.gh`), it's almost always a single **CNAME** record pointing at `ghs.googlehosted.com`. Treat the command's own output as the source of truth over that, in case Google's mapping details differ for your project.

If you'd rather not wait on the command, `gcloud run domain-mappings describe --domain=admissions.tcsch.edu.gh --region=$REGION` re-prints the same record info afterward.

## 12. Cloudflare DNS record

In the Cloudflare dashboard, under the `tcsch.edu.gh` zone → DNS:

| Type | Name | Target | Proxy status |
|---|---|---|---|
| CNAME | `admissions` | `ghs.googlehosted.com` (confirm exact value from step 11's output) | **DNS only** (grey cloud) |

Leave it grey-clouded (not proxied) until `gcloud run domain-mappings describe --domain=admissions.tcsch.edu.gh --region=$REGION` shows the mapping's certificate status as `Ready` — Cloud Run's managed cert provisioning does a validation check that a Cloudflare-proxied (orange cloud) record can interfere with. Once it's `Ready`, you can switch it to **Proxied** (orange cloud) to get Cloudflare's TLS/DDoS/rate-limiting layer in front, as `shared-stack.md` calls for — Cloudflare set to **Full (strict)** SSL mode at that point, so it validates Cloud Run's real cert rather than accepting anything. **The rate-limit rule below only takes effect once this record is proxied (orange cloud)** — Cloudflare can't see, let alone rate-limit, traffic to a grey-clouded (DNS-only) record.

## 13. Cloudflare rate-limit rule on `/api/admissions/*`

Dashboard config, not code — this is genuinely a Cloudflare-side step, not something `manage.py`/DRF settings can do, since the point is to stop abusive traffic *before* it reaches Cloud Run at all. (Django-side, `DEFAULT_THROTTLE_RATES` in `settings.py` already rate-limits per-endpoint at the application layer — `20/hour` anonymous, `120/hour` for draft autosaves — this Cloudflare rule is a second, earlier line of defense, not a replacement.)

In the Cloudflare dashboard, for the `tcsch.edu.gh` zone:

1. Go to **Security** → **Security rules** → **Create rule** → **Rate limiting rules**.
2. **Rule name**: something like `admissions-api-rate-limit`.
3. **Field**: `URI Path`, **Operator**: `starts with`, **Value**: `/api/admissions/`. (Add a second condition — **Hostname** `equals` `admissions.tcsch.edu.gh` — if this zone ever hosts more than this one subdomain.)
4. **When rate exceeds**: start with something like `60` requests per `1 minute` — comfortably above real traffic (the app's own tightest limit is 20/hour *per client*, but this Cloudflare rule counts *all* clients together for the path, so it needs headroom) but well below what a scripted bot would produce.
5. **With the same characteristics**: `IP address` (default) — counts each visitor's requests separately.
6. **Choose action**: `Block` (or `Managed Challenge` if you'd rather give a real burst of legitimate traffic a chance to prove itself before blocking).
7. **Duration**: how long the block/challenge lasts once triggered — `10 minutes` is a reasonable start.
8. **Deploy**.

The free Cloudflare plan allows exactly **one** rate limiting rule per zone — this is it, so don't create a second one for something else without upgrading. Watch **Security** → **Events** for a day or two after deploying to confirm it's not accidentally catching real parents (e.g. the multi-step Application form's autosaves) before trusting it unattended.

## After this works

- Redeploying later is just steps 4 and 7 again (rebuild, push, `gcloud run deploy`) — plus step 5's migrate job if the new code has migrations, and step 5b's storage-config job if upload limits changed. Note: pass env-var flags **only** for vars you intend to change, and prefer `--update-env-vars` over `--set-env-vars` so the deploy doesn't wipe the ~20 vars it omits (the 2026-09-02 CORS deploy used `--update-env-vars='^##^KEY=a,b##KEY2=...'`). Reusing this doc's full step-7 command verbatim would overwrite real values with its placeholders (`SUPABASE_URL=https://your-project.supabase.co`, `TURNSTILE_SITE_KEY=PASTE_...`).
- Before using Phase 6's bulk email for real: `admissions.can_send_bulk_email` needs to be granted to whichever staff member(s) should actually be able to trigger a send — not automatic, same deliberate-grant pattern as `can_view_health_info`. (Sending-domain verification — step 6b — is already done.)
- The Lead-capture widgets (Phase 6, 2026-09-01) on the marketing site: the production Turnstile widget's allowed-domains list already includes `tcsch.edu.gh`, `www.tcsch.edu.gh`, and `tcsch.vercel.app`, verified by a real production submission on 2026-09-03 — see **Current deployment state** above. Add any further preview hostnames to the same "TCS OS" widget as they come up.
