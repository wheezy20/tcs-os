# TCS Admissions Module — Schema & API

**Status:** active reference. Update whenever a real schema or endpoint decision is made or changed. Assumes the shared infrastructure in `../shared-stack.md` (Django project, Supabase, RBAC pattern, hosting, Cloudflare), this file only covers what's specific to admissions.

## Where this lives in TCS OS

- Django app: `backend/admissions/`
- Storage bucket: `admissions-documents` (namespaced within the shared Supabase project)
- Public form subdomain: `admissions.tcsch.edu.gh`

## Phase 1 domain model

```
Family (referral_source, comments)
 ├── Parent/Guardian ×1-2 (first_name, surname, email, phone, relationship, religion, address, town/city)
 └── Student ×1-5 (name, DOB, current school, current grade, student_id)
      └── Application  (one per student — academic_year, grade applied for,
                         month of enrollment, inquiry_reference, application_reference)
           ├── ApplicationStage  (Inquiry → Application → Document Review → Offer → Enrolled,
           │                      or a terminal Waitlisted / Rejected / Offer Declined)
           ├── Decision          (accepted/waitlisted/rejected, decided_by, decided_at, notes)
           ├── Offer             (token, response: pending/accepted/declined/expired, expires_at)
           ├── Document          (type, file_path in Supabase Storage, status: required/pending_review/approved/rejected)
           └── Notes (internal, staff-only)

Capacity (academic_year, year_group, capacity) — seats per grade per year;
backs a soft over-capacity warning in admin, see Phase 3 below.

ReferenceCounter (key, next_value) — not part of the family tree; backs the
sequential portion of inquiry_reference/application_reference/student_id.
```

A single inquiry submission can cover multiple children (siblings) and up to two guardians in one call — see `InquirySerializer` in `admissions/serializers.py` for the exact nested shape.

Django models (rough, refine when building):

- `Family`
- `Guardian` (FK to Family) — name is stored as `first_name`/`surname`, not a single `full_name` field (that was the original Phase 1 shape; split via migration once the real TCS form asked for them separately — `full_name` survives only as a read-only property that joins the two)
- `Student` (FK to Family) — `student_id` is the permanent ID, assigned once at enrolment (see Phase 2.5 below), not at creation
- `Application` (FK to Student; stage field; academic_year; year_group applied for; `inquiry_reference`/`application_reference`, see Phase 2.5)
- `Document` (FK to Application; type; file_path — a Supabase Storage object path, not a URL; status)
- `Note` (FK to Application; staff-only)
- `ReferenceCounter` — not part of the Family/Student/Application tree; a small counter table that backs the three reference-number sequences above (see Phase 2.5)
- `Decision`, `Offer`, `Capacity` — see Phase 3 below

**Explicitly deferred** to later phases (see `03-build-order.md`): assessments, interviews, review rubrics, waitlist/capacity logic, offers/payments, re-enrolment, campaigns/lead scoring, workflow engine, AI analytics, alumni.

## Phase 1 API surface

```
POST   /api/admissions/inquiries/     (public — creates Family + Student(s) + Application(s) at "Inquiry" stage)
```

## Phase 2 API surface

```
POST   /api/admissions/applications/  (public, no prior Inquiry required — see matching rules below)
POST   /api/admissions/upload-url/    (public — mints a signed Supabase Storage upload URL for one document)
```

## Phase 3 API surface

```
POST   /api/admissions/offers/<token>/respond/  (public — the offer's own token is the entire access
                                                  control, same trust model as the two endpoints above)
```

## Phase 5 API surface

```
POST   /api/admissions/application-drafts/                 (public — create a draft, returns a token)
GET    /api/admissions/application-drafts/<token>/          (public — resume: fetch saved progress)
PATCH  /api/admissions/application-drafts/<token>/          (public — save progress)
POST   /api/admissions/application-drafts/<token>/submit/   (public — final submit, via ApplicationSerializer)
```

Everything else (staff review, stage changes, document approval) happens through Django admin, no separate staff-facing endpoint or UI needed yet.

### Application dedup / matching (no auth, no parent portal yet)

There's no login for parents, so `ApplicationSerializer.create()` does best-effort
server-side matching rather than requiring the parent to identify themselves:

1. Guardian email (case-insensitive, exact) → existing `Family`, if any; otherwise a new `Family` is created.
2. Each submitted guardian is upserted onto that family by email (existing guardian's fields get updated, not duplicated).
3. Student match within that family by `full_name` (case-insensitive) + exact `date_of_birth`.
4. If the matched student already has an `Application` for the same `academic_year` + `year_group_applied_for` (e.g. an Inquiry-stage one), that row is advanced to `stage="application"` rather than creating a second row for the same student/year/grade.

**Known limitations, accepted for Phase 2** (matches `01-vision.md`, which lists
"Duplicate detection" as a later-phase module): a typo'd/different email creates
a genuine duplicate `Family` with no merge tooling to fix it; identical-name
twins born the same day would collide on the student match. Both are rare and
staff-correctable via admin for now.

### Document uploads — signed URL, not a Django proxy

The bucket (`admissions-documents`) is private. Flow: browser asks Django for a
signed upload URL (`POST /upload-url/`, `SUPABASE_SERVICE_ROLE_KEY` never leaves
the server) → Django generates the storage path itself, server-side, so a client
can't write outside its own namespace → browser `PUT`s the file bytes directly to
Supabase, no key of any kind required for that step (the signed token in the URL
is self-authorizing) → the resulting `file_path` goes into the final
`applications/` submission.

Supabase's signed-*download*-URL endpoint 404s if called before the object
exists, so `Document.file_path` stores the bucket-relative **path**, not a URL —
a signed read URL is minted fresh on demand each time admin renders a document
link (`admissions/storage.py:create_read_url`), rather than generated once and
stored, which would also eventually expire silently.

### Upload constraints — type/size, enforced at three layers (2026-08-26)

Allowed types: PDF, JPG/JPEG, PNG (`admissions/storage.py:ALLOWED_UPLOAD_EXTENSIONS`
/ `EXTENSION_MIME_TYPES`). HEIC was considered (iPhones default to it) and
deliberately excluded — Safari renders it but most other browsers don't, so a
staff member reviewing documents in admin could hit an unviewable file.
Revisit if parents actually run into this. Max size: `MAX_UPLOAD_SIZE_MB`
(default 10MB).

Enforced in increasing order of trust, since the earlier layers can be
bypassed by a client that doesn't run the app's own JS:
1. **Browser** — `application.html`'s `handleFileSelected` checks extension
   and `file.size` before even calling `/upload-url/`. Instant feedback,
   trivially bypassed (not real security).
2. **`UploadURLRequestSerializer`** — rejects a bad `filename` extension or a
   `file_size` over the limit before a signed URL is even minted. Still
   trusts the client-declared `file_size`, so a lying client could request a
   URL for a "small" file and then PUT a large one.
3. **The Supabase bucket itself** (`file_size_limit` / `allowed_mime_types`,
   set via `manage.py configure_storage_bucket` → `storage.configure_bucket_limits()`)
   — checks the real bytes/Content-Type on the actual `PUT`, so it's the only
   layer that can't be bypassed this way. Confirmed empirically: a signed URL
   requested with a fake small `file_size` still gets a real `413
   EntityTooLarge` from Supabase when an 11MB file is actually PUT, and a PUT
   with `Content-Type: application/x-msdownload` gets a real `415
   InvalidMimeType` — independent of what the app itself checked.

`configure_storage_bucket` is not run automatically on deploy; it must be run
once per environment (and again if `MAX_UPLOAD_SIZE_MB` or the allowed
extensions change) — see `docs/deployment.md`.

### Email notifications

One parent confirmation + one internal staff alert per submission event (not
per child — a 2-sibling Inquiry still sends exactly 2 emails). Plain text,
`admissions/emails.py`, sent via `EMAIL_BACKEND` (console in dev). Failures are
logged and swallowed, never block the actual Inquiry/Application from saving.

Since 2026-09-03 the Inquiry / Application / draft-resume emails don't send
*during* the request — they're rendered into `TransactionalEmail` rows and
delivered by a Cloud Task. See "Transactional email — off the request cycle"
below. Offer and Lead-capture emails stay synchronous.

## Phase 2.5 — reference ID numbering

**Provisional pending confirmation with TCS admissions/admin staff** on whether
a legacy paper-records numbering convention already exists (see `03-build-order.md`).
Student ID's format below was specified directly rather than proposed, so it's
on firmer ground than the Inquiry/Application format, but neither has been
checked against real legacy records yet.

| ID | Format | Example | Assigned when |
|---|---|---|---|
| Inquiry reference | `INQ-YYYY-NNNN` (YYYY = submission year) | `INQ-2026-0042` | `Application` created at `stage="inquiry"` |
| Application reference | `APP-YYYY-NNNN` (YYYY = submission year) | `APP-2026-0042` | `Application.stage` first reaches `"application"` or later (direct entry or advanced from Inquiry) |
| Student ID | `YYPPNNNN` (YY=year, PP=classification, NNNN=roll number) | `26010002` | `Application.stage` first reaches `"enrolled"` — once per `Student`, never reassigned |

Classification codes (`PP`), keyed by the grade enrolled *at* (`year_group_applied_for`
on the enrolling Application, not `current_grade`): `01` Preschool (Pre Nursery–Nursery 2),
`02` KG, `03` Primary (Grade 1-6), `04` JHS (Grade 7-9). **No `05` code for
SHS (Grade 10-12)** — TCS doesn't offer it yet. See the `TODO(SHS)` in
`admissions/models.py::STUDENT_ID_CLASSIFICATION`: a student enrolled at
Grade 10-12 gets no `student_id` assigned (not a guessed code) and a logged
warning, surfacing as a visibly-missing field in admin rather than a silently
wrong one.

All three sequences reset per year (Student ID additionally per classification
within that year), backed by `ReferenceCounter` — a small counter table
incremented under `select_for_update()` so concurrent submissions can't be
handed the same number. Assignment logic lives in `Application.save()`, so it
fires the same way regardless of entry point: the public serializers, a
manual admin edit, or the bulk stage-transition actions in `ApplicationAdmin`
(which specifically iterate + `.save()` rather than `queryset.update()`, since
the latter bypasses `save()` entirely and would silently skip assignment).

Interaction with Phase 2 dedup matching: since these fields live on the same
`Application`/`Student` rows that matching already finds-and-reuses, a
returning family's re-application automatically keeps its existing reference
numbers and Student ID — assignment only ever fires once, guarded by "still
null," so there's no special-casing needed for the matched-row case.

Existing Phase 1/2 test data (~12 `Application`/`Student` rows) was
deliberately left with `NULL` reference numbers rather than backfilled — the
migration is schema-only, no business logic mixed in.

### Resetting test data before go-live

`python manage.py reset_admissions_data` — truncates every admissions table
(`Family`, `Guardian`, `Student`, `Application`, `Document`, `Note`,
`ReferenceCounter`) and resets their ID sequences back to 1, via a single
`TRUNCATE ... RESTART IDENTITY CASCADE`. Destructive and irreversible by
design — it exists specifically to wipe accumulated Phase 1/2/2.5 test data
immediately before the app goes live, so reference numbers and Student IDs
start clean at `0001` for real families. Always prompts for confirmation
(type `yes`); there is no flag to skip the prompt. Built but intentionally
never run — see `admissions/management/commands/reset_admissions_data.py`.

## Phase 3 — decisions, offers, gated enrollment

### Gate mechanism, shared between two stages

`Application.GATED_STAGES = {"offer", "enrolled"}`. Entering either requires
a prior step, checked in `Application.save()` itself (not `.clean()`, not an
admin-only check) so it's enforced regardless of entry point — the public
serializers, a manual admin edit, or the bulk stage-transition actions:

- **`offer`** requires an accepted `Decision`.
- **`enrolled`** requires an accepted `Decision` *and* an accepted `Offer`
  (an `Offer.refresh_expiry()` call happens as part of this check, so a
  quietly-expired offer resolves to `expired` — and blocks — right here,
  not just when something else happens to notice).

Both share one mechanism (`_requirement_met_for_stage`), not duplicated gate
logic per stage — see `Application.save()`, `_has_accepted_decision()`,
`_has_accepted_offer()`.

### Negative outcomes propagate automatically; positive outcomes don't

`Application.STAGE_CHOICES` gained three terminal values: `waitlisted`,
`rejected`, `offer_declined`. The rule, applied consistently in both
`Decision.save()` and `Offer.save()`:

- A **negative/terminal** outcome (`Decision.decision_type` becoming
  `waitlisted`/`rejected`; `Offer.response` becoming `declined`/`expired`)
  pushes `Application.stage` to match, automatically — no separate staff
  step, since these outcomes are unambiguous and final.
- A **positive** outcome (`decision_type="accepted"`; `response="accepted"`)
  never auto-advances the stage — it only unlocks the next gate for a
  deliberate staff action (Generate Offer; Mark Enrolled). Accepting an
  Offer doesn't enrol anyone by itself.

Waitlist promotion is therefore a manual two-step staff action: change
`Decision.decision_type` from `waitlisted` to `accepted`, then Generate
Offer — no automated promotion exists, deliberately, so a human always
decides who fills an opening. Tested end to end (a follow-up session, after
the initial Phase 3 pass had only checked this "by inspection"): a
`waitlisted` Decision changed to `accepted` correctly leaves `stage` at
`waitlisted` (accepted doesn't auto-advance), Generate Offer then succeeds
from that stage precisely because the gate checks `decision_type`, not
current `stage`, and the rest of the chain (parent accepts, Mark Enrolled)
proceeds exactly as it would from any other route into `offer`.

### `Offer` — token, expiry, and why expiry is lazy

Same public-token trust model as the rest of the public surface (no parent
portal exists). `expires_at` is set when an offer is generated
(`OFFER_EXPIRY_DAYS`, default 14, env-configurable). There's no scheduler in
this project (no Celery, no cron, nothing deployed at all yet), so expiry
can't be a timed job — `Offer.refresh_expiry()` resolves `pending → expired`
lazily, called wherever the response is about to matter (the enrolled-stage
gate, the public respond endpoint, admin display). Mutable/reusable
(`OneToOneField`): re-offering after a decline or expiry means resetting the
same row (admin's "Reset Offer" action), not creating a second `Offer`.

### A real bug found during testing — stale cached instances

`Decision.save()`/`Offer.save()`'s propagation logic originally checked
`self.application.stage` directly. That broke when the save was triggered
*from inside* `Application.save()`'s own gate check — e.g. the admin's
`_bulk_set_stage` sets `application.stage = "enrolled"` **before** calling
`.save()`, so by the time the nested `Offer.save()` (via
`refresh_expiry()`) ran its propagation check, `self.application` was the
*same* Python instance the caller had already mutated in memory — reading
`"enrolled"`, not the true prior `"offer"`, and silently skipping the
propagation. Fixed by always fetching a fresh `Application` row
(`Application.objects.get(pk=self.application_id)`) for this check instead
of trusting a possibly-stale cached instance — the same "never trust
in-memory state, always re-fetch" pattern already used for
`previous_stage`/`previous_response`/`previous_type` throughout this file.
Caught by the offer-expiry test specifically; worth remembering if this
propagation logic is ever touched again.

### `admissions.can_decide` permission

A plain Django `Meta.permissions` entry on `Decision` — not a new
role/profile system. Verified before building that literally nothing else
existed (no Groups, no custom permissions, no Profile model — checked
directly in code, not assumed). Gates the `DecisionInline`/`OfferInline`
(`has_add_permission`/`has_change_permission`) and the `generate_offer`/
`reset_offer` admin actions via `get_actions()`, which Django's own action
dispatch treats as a functional block (the action name won't even be
recognized if submitted directly), not just a hidden dropdown option.
Assigning the permission to real staff is a one-time manual admin task
(create a Group, check the box, add users) — no UI was built for it.

### `Capacity` — soft warning, not a hard block

`ApplicationAdmin._warn_if_over_capacity()`, called from `save_formset()`
right after a `Decision` is saved: if `decision_type == "accepted"` and a
`Capacity` row exists for that application's `(academic_year,
year_group_applied_for)`, it counts all `accepted` Decisions for that same
(year, grade) and shows a `messages.WARNING` naming the count and the
capacity if the count exceeds it. Never blocks the save — real admissions
has legitimate reasons to go over on paper (sibling priority, board
exceptions). Silent if no `Capacity` row is defined for that (year, grade)
at all — there's nothing to compare against — and silent for
`waitlisted`/`rejected` decisions, which don't consume a seat.

This was initially scoped in the Phase 3 plan but shipped as a follow-up:
the first Phase 3 pass only built the `Capacity` model as storage (its
docstring briefly, incorrectly, claimed the warning already existed —
caught and corrected during a documentation-accuracy pass before this was
actually built).

## Phase 4 — branding & emailed collateral

Full brand guide followed precisely — colors, logo usage rules, typography —
not placeholder design. Source of truth: `docs/admissions/brand-tokens.md`
and the 6 logo PNGs in `frontend/assets/branding/`.

### Admin dashboard (django-unfold)

- `SITE_LOGO`/`SITE_ICON` in `UNFOLD` settings point at **callables**
  (`admissions/branding.py`), not hardcoded path strings — the callables
  call Django's `static()` at request time. This matters specifically
  because of `whitenoise.storage.CompressedManifestStaticFilesStorage`:
  production serves content-hashed filenames, resolved via the manifest
  `collectstatic` builds, and only `static()` (not a hand-built string)
  resolves that correctly in both dev and prod.
- Light/dark theme both correctly swap logo variant per the brand guide's
  critical rule (mint-leaf laurel on white / lime laurel on dark) — unfold's
  `site_logo.html` already supports a `{"light": ..., "dark": ...}` dict for
  exactly this, no custom template needed for the header/sidebar logo.
- `COLORS.primary` is an 11-stop ramp interpolated between three real brand
  colors (Jungle Mist → Deep Jungle Green → Deep Teal Shadow → near-black),
  computed rather than hand-picked, and checked as a rendered swatch strip
  before use. unfold's `convert_color()` accepts plain hex directly.
- **Login page**: `backend/templates/admin/login.html` overrides unfold's
  own `admin/login.html` — via `TEMPLATES[0]["DIRS"]` (searched before app
  template dirs) plus `{% extends "admin/login.html" %}` from within the
  override itself, which Django's template loader resolves to the *next*
  match in the search chain (unfold's version), not an infinite loop. This
  is why: unfold's `LOGIN.image` config renders as a `background-image:
  cover` on a large side panel, designed for a full-bleed photo — forcing a
  logo mark (mostly transparent negative space) into that slot would crop
  it badly. The override instead prepends the vertical logo (again
  light/dark-swapped) above `{{ block.super }}`, keeping all of unfold's
  own markup intact rather than duplicating it.
- Brand assets live in **two places**, not one: `frontend/assets/branding/`
  (used directly by the standalone HTML forms below) and
  `backend/admissions/static/admissions/branding/` (Django's static
  pipeline). The plan was to point `STATICFILES_DIRS` at the frontend copy
  to avoid duplication, but the Dockerfile's `collectstatic` runs inside a
  `backend/`-scoped build context that may not include the sibling
  `frontend/` directory depending on how the image gets built — a
  cross-directory static dir would work in local dev and silently break on
  the first real deploy. Caught before implementing, not after. If the
  brand assets are ever updated, both copies need updating.

### Public forms

`index.html`, `application.html`, `offer.html` — all three got the
identical treatment: Google Fonts `<link>` (Cinzel for `h1`/`legend`, DM
Sans for everything else), the same brand color palette as the admin
(Deep Jungle Green primary, Mint-Leaf-tinted success state), and a
horizontal-logo header. Still fully dependency-free, no build step. One gap
found and fixed while doing this: browsers don't inherit `font-family` on
buttons/form controls by default, so every button rule across all three
files needed an explicit `font-family: inherit` or DM Sans would only have
applied to text, not button labels.

### Email attachments

`admissions/emails.py`'s `_send()` switched from `send_mail()` to
`EmailMessage` specifically for attachment support. Generic on purpose, per
the explicit ask — `ADMISSIONS_ATTACHMENTS_DIR` (a directory) plus a
per-email-type filename list rather than hardcoding any one document. A
missing configured file is logged and skipped, never blocks the send (same
"never let email plumbing block the actual submission" rule as the rest of
this module). Two lists are wired up now:

- `INQUIRY_EMAIL_ATTACHMENTS` — default `["admissions-overview-and-fees.pdf"]`
  (2026-09-03), so the Inquiry **parent** confirmation carries the Admissions
  Overview & Fees PDF. The staff-alert half of the same submission event does
  **not** get the attachment — only `_send()` calls that pass the list do.
- `PDF_GATE_ATTACHMENTS` — same default file, for the PDF-gate download email
  (Phase 6 Lead capture).

Both resolve the same shipped-in-the-image file (`backend/admissions/attachments/admissions-overview-and-fees.pdf`, ~4.9 MB). Extending attachments to Application/Offer emails later is still a one-line change — pass another settings list into that email's `_send()` call.

## Phase 4.5 — deployment prep

Closes the Phase 1 "still not done" item (real subdomain, Cloudflare). Full
step-by-step: `docs/deployment.md`.

### Routing

```
GET    /              (302 → /inquiry)
GET    /inquiry        public inquiry form
GET    /apply           public application form
GET    /offer           parent-facing offer accept/decline page
```

Served directly by Django (`tcs_os/urls.py`, plain `TemplateView`s) rather
than a separate static host — see the revised "Frontend pattern" note in
`docs/shared-stack.md`. Templates live in `backend/templates/public/`,
adapted from `frontend/*.html`: the branding image goes through `{% static
%}` (reusing the same file already duplicated into `admissions/static/` for
Phase 4, not a third copy) and the JS API base URL is root-relative
(`/api/admissions/...`) instead of the old hardcoded `127.0.0.1:8000`.
`frontend/*.html` are unchanged, kept for a quick local preview outside
Django — a second copy of each form's markup that needs manual sync on
future edits, same tradeoff already accepted for the branding PNGs.

`emails.py`'s offer link changed from `/offer.html?token=` to
`/offer?token=` to match.

### Email

`EMAIL_BACKEND` now switches on `RESEND_API_KEY`: set, it uses Django's
built-in SMTP backend against Resend's SMTP relay
(`smtp.resend.com:587`, username literally `"resend"`); unset, it falls
back to the console backend exactly as before. Chose plain SMTP over
`django-anymail` — Resend supports SMTP directly (confirmed against its own
docs, not assumed from the SendGrid precedent), so the same no-new-dependency
reasoning holds; nothing here needs anymail's HTTP-API-only features
(tracking, dynamic templates) yet.

### Dockerfile

Reviewed *and* verified with a real `docker build` + `docker run`, not read
through — this found two real, previously-untested bugs:
- `collectstatic` (already present) needs `SECRET_KEY`/`DATABASE_URL` just
  to import settings — neither had a build-time value, so the image had
  never actually been buildable. Fixed with placeholder values scoped to
  that one `RUN` step's environment, not baked into the image.
- The added non-root user's home directory (`useradd -r` alone points
  `$HOME` at `/home/django` without creating it) caused a real `Permission
  denied` from gunicorn's control socket under `docker run`. Fixed by
  setting home to `/app` explicitly.

Also dropped `build-essential`/`libpq-dev` — `psycopg2-binary` is a
prebuilt wheel, nothing in `requirements.txt` needs a compiler.

## Phase 5 — expanded Application form

Full plan (schema, draft/resume design, the Campus×Capacity tradeoff) reviewed
and approved by the user before any code was written — see `04-build-log.md`
for the exact resolutions this section assumes.

### Schema

- `Student` gains `gender`, `nationality`, and its own address block
  (`address`, `town_city`, `postal_code`, `country`) — previously only
  `Guardian` had an address at all. Also `previous_school_location`, distinct
  from `current_school` (the school's *name*). All `blank=True` — Inquiry
  still doesn't collect them, only Application does.
- New `Campus` model (`Main`, `Annex`, seeded via a data migration). Annex
  only accepts Pre Nursery/Nursery 1 — enforced in
  `ApplicationSerializer.validate()` via a hardcoded grade set
  (`ANNEX_ACCEPTED_GRADES`), the same pattern `PRESCHOOL_GRADES`'s
  vaccination check already used, checked by `Campus.name` (see the model's
  own docstring for the tradeoff: renaming that row in admin silently
  disables the check).
- New `EmergencyContact` (plain FK, `related_name="emergency_contacts"`) and
  `HealthInfo` (OneToOne) — both scoped to **Application**, not Student or
  Family, specifically so they appear as inlines on the same
  `ApplicationAdmin` page staff already use for Decision/Offer/Document/Note,
  rather than requiring a second admin screen.
- `HealthInfo` is real child health data — restricted behind a new
  `admissions.can_view_health_info` permission (`Meta.permissions`, same
  mechanism `Decision.can_decide` already uses). `HealthInfoInline` overrides
  `has_view_permission`/`has_add_permission`/`has_change_permission`/
  `has_delete_permission` to all check that permission, so staff without it
  don't see the section exists at all. It's never in `list_display`,
  `search_fields`, or an export. **Nobody has this permission by default** —
  granting it to a role is left as a deliberate decision for the user, not
  auto-assigned to the existing Admissions Officer group.
- `Capacity` gains a `campus` FK, `unique_together` now
  `(academic_year, year_group, campus)` — TCS's two campuses are physically
  separate seat pools. A null campus means "not campus-specific." Postgres
  treats NULL as never equal to itself, so two `campus=NULL` rows for the
  same (year, grade) aren't actually prevented by that constraint —
  acceptable at this table's scale (hand-managed by admin staff), not worth
  a partial unique index. `ApplicationAdmin._warn_if_over_capacity` filters
  by campus too now; verified independently (a Main grade going over its own
  cap doesn't warn against Annex's separate cap for the same grade/year, and
  vice versa).
- `Application` gains `campus` (FK, nullable — existing rows have none),
  `wants_scholarship_info`/`scholarship_interest_details`, and the
  declaration fields below. No new model for scholarship — not a distinct
  sensitive category, low complexity, lives directly on Application.
- `Document.TYPE_CHOICES` gains `application_fee_proof` — reuses the
  existing Document model entirely for the new GHS 200 application fee
  (offline bank transfer/mobile money only; a real payment portal is future
  scope, not attempted here). Same `required → pending_review → approved/
  rejected` review workflow every other document already has. Not hard-
  enforced server-side (unlike vaccination for preschool) — consistent with
  how `financial_clearance`/`previous_report` are already optional-but-
  expected, not gated.
- Declaration is **two independent consents**, not one: `declaration_agreed`
  (indemnity/data-protection/accuracy, required to submit) and
  `media_consent_agreed` (separate, optional, default unchecked — the
  media-consent text is independently revocable per its own wording, so it's
  a distinct field rather than folded into the main declaration). Plus
  `declaration_signature_name`, `declaration_agreed_at` (set server-side at
  submit, not user-entered), and `declaration_ip_address` (best-effort audit
  trail — `REMOTE_ADDR` may reflect a Cloudflare/Cloud Run proxy hop rather
  than the parent's real IP, not a verified identity).
- `campus` is looked up by **name** (`SlugRelatedField`), not primary key —
  the frontend's two campus options are hardcoded static HTML (`Main`/
  `Annex`, matching the dependency-free no-build-step pattern the rest of
  these forms use), so it only ever knows Campus by name, never a DB id.

### Draft/resume mechanism

New `ApplicationDraft` model — same token generator and trust model as
`Offer`'s existing resume link (an unguessable token *is* the access
control, no parent login exists). Stores the whole in-progress multi-step
form as a JSON blob (`data`) rather than partial real `Student`/`Guardian`
rows: a draft can be genuinely incomplete or invalid at any point (no email
yet, a malformed date), and creating partial real rows for that would either
fail validation or pollute the real tables. Full validation only ever
happens once, at final submit, through the *same* `ApplicationSerializer` a
direct `/applications/` POST uses — one source of truth for what "valid"
means, not a second looser one for drafts.

```
POST   /api/admissions/application-drafts/                 create, returns a token
GET    /api/admissions/application-drafts/<token>/         resume — fetch saved data + current_step
PATCH  /api/admissions/application-drafts/<token>/         save progress (autosave on every step, or explicit "save for later")
POST   /api/admissions/application-drafts/<token>/submit/  final submission — runs data through ApplicationSerializer
```

`expires_at` resolved lazily (`ApplicationDraft.is_expired`), same pattern as
`Offer.refresh_expiry()` — no scheduler exists in this project.
`DRAFT_EXPIRY_DAYS` defaults to 30 (longer than `OFFER_EXPIRY_DAYS`'s 14 —
gathering documents can take a parent weeks). The "email me a resume link"
action is explicit (a distinct button), separate from the silent autosave
that happens on every step — autosaving on every keystroke-level save would
spam the parent's inbox otherwise.

This is a deliberate stopgap, not a replacement for a real parent
login/portal — flagged as a known future need in `01-vision.md`.

### Frontend

`application.html` rebuilt as a 6-section form (Guardian(s) → Student →
Emergency Contact → Health/Wellbeing → Documents & Payment → Declaration),
still fully dependency-free (no build step, no framework) — section
visibility is plain JS show/hide over what were already fieldsets. The
student's "same address as guardian" checkbox defaults **checked** (opposite
of guardian 2's own equivalent checkbox, which defaults unchecked) — most
students live with guardian 1, so default to that and let the parent
uncheck if different. Only `address`/`town_city` are copied from guardian 1;
`postal_code`/`country` have no guardian-side equivalent to copy from, so
they stay independently editable regardless of the checkbox.

**Navigation is free, not linear** (a follow-up the same day): a sidebar
(desktop, sticky) / horizontal scrollable bar (mobile, pinned above the
form) lists all 6 sections, each showing a checkmark once its required
fields are filled — clickable in any order, with no mid-form validation
blocking, consistent with the draft/resume system's own premise. `goToStep()`
is the one entry point for every kind of navigation and autosaves in both
directions. Completeness is computed per-section via `isStepComplete()`,
checked against `el.hidden` rather than `el.offsetParent === null` — the
latter reads `null` for every field inside *any* non-visible section, not
just a deliberately-hidden one, which would have made every section but the
current one falsely report complete. Final submit checks every section at
once and, if incomplete, shows exactly which ones with a jump-back link
per section — server-side 400s get the same treatment, mapped from error
key to section (`stepForErrorKey`) so a validation failure lands the parent
somewhere useful.

Two real bugs surfaced by testing this, not before: the shared
`AnonRateThrottle` (20/hour) started 429ing during testing since free
navigation autosaves far more often than the old fixed linear flow did —
`saveDraft()` also never checked its response status, so a throttled or
failed save silently reported success. Fixed with a dedicated
`DraftRateThrottle` (`application_draft` scope, 120/hour) on just the
create/save-progress endpoints — not final submit, which keeps the tight
default anti-abuse rate — and `saveDraft()`/`ensureDraftToken()` now return
`true`/`false` so a failure is actually shown. Separately, the sidebar's
desktop-only `flex: 0 0 210px` (fixing its *width*) was never reset for
mobile, where `.layout` switches to `flex-direction: column` — the same
rule then fixed the nav's *height* at 210px, rendering six huge stacked
boxes instead of a compact strip; caught from an actual mobile screenshot.

## Phase 5 follow-up — Turnstile, nationality, academic year, submission latency (2026-08-26)

### Cloudflare Turnstile

`admissions/turnstile.py`'s `verify_turnstile_token(token, remote_ip=None)` does the real server-side check — a POST to Cloudflare's `siteverify` endpoint. Wired in at the view level (not a serializer field) on the four endpoints that actually create/mutate a real record: `InquiryCreateView`/`ApplicationCreateView` (via a small `TurnstileProtectedCreateMixin.create()`), and `ApplicationDraftSubmitView.post()`/`OfferRespondView.post()` directly. Deliberately **not** applied to the draft save/autosave endpoints — those don't create anything a bot benefits from, and requiring a fresh solve on every autosave would be a real UX cost for a real parent over a multi-minute form.

`TURNSTILE_SITE_KEY`/`TURNSTILE_SECRET_KEY` default to Cloudflare's own published test keys (`1x00000000000000000000AA` / `1x0000000000000000000000000000000AA`, both "always passes") — not empty/disabled, so local dev exercises the real Cloudflare round trip with no account of its own. **Must be replaced with real keys from a real Turnstile site before this offers any actual bot protection** — the test keys are public knowledge.

The site key is rendered into `templates/public/*.html` from settings via `extra_context` on each `TemplateView` in `tcs_os/urls.py` (`{{ TURNSTILE_SITE_KEY }}`), not baked in by the usual `frontend/*.html` → `templates/public/*.html` sed pipeline — a deliberate deviation from that pipeline's usual literal-substitution pattern, since the site key is public (safe to render from settings, no reason to hand-copy it) and Django is already templating these files (`{% load static %}` proves it). `frontend/*.html` (the non-Django static preview copies) hardcode the same default test key as a plain literal instead, consistent with how they hardcode their own dev-only `API_BASE`.

Two non-obvious things found only by actually testing this, not by reading Cloudflare's docs:
- The widget container **must not** have Turnstile's own `cf-turnstile` class if you're rendering it explicitly with `turnstile.render()` and a custom `callback` — that class makes Cloudflare's script auto-scan and implicitly render the widget on page load, which wins the race and silently skips the explicit `render()` call (console warning: "already exists in this container"). An implicit render never wires up the custom callback, so the JS-side token variable stays empty forever even though the widget visibly works and the DOM's hidden `cf-turnstile-response` input does get populated — the bug is invisible unless you check the JS variable specifically, not just "does a token appear somewhere."
- On `application.html`'s multi-step form, the widget is rendered lazily — only once the Declaration step (the only step with a Submit button) actually becomes visible, not eagerly at page load. Cloudflare's iframe doesn't size itself correctly inside a `[hidden]` (`display:none`) container, which every step but the first is at load.

Tokens are single-use — every submit handler calls `turnstile.reset()` after an attempt (success or failure) so a retry gets a fresh token instead of a "token already spent" rejection.

### Nationality — country dropdown

`application.html`'s `NATIONALITY_OPTIONS` (Student form) is the ISO 3166-1 country list, Ghana pinned first, fetched from `lukes/ISO-3166-Countries-with-Regional-Codes` on GitHub rather than hand-typed. A handful of ISO's official long-form names were swapped for their common name for this parent-facing field (`"Korea, Republic of"` → `"South Korea"`, `"Viet Nam"` → `"Vietnam"`, `"United States of America"` → `"United States"`, etc. — see the code comment for the full list). No model/serializer change — `Student.nationality` was always a free `CharField`, so this is purely a frontend input constraint.

**2026-09-03 — trimmed and given typeahead:** eight ISO entries with no permanent civilian population (so no nationality anyone actually holds) were removed — Antarctica, Bouvet Island, British Indian Ocean Territory, French Southern Territories, Heard Island and McDonald Islands, Pitcairn, South Georgia and the South Sandwich Islands, United States Minor Outlying Islands — leaving **241 entries**. Everything with a resident population (Guam, Puerto Rico, Åland, Hong Kong, Taiwan, Palestine, the small Pacific/Atlantic island territories, …) is kept — "no permanent population" is the one objective line, anything past it is an arbitrary population-threshold judgment. The `<select>` became a native `<input list="nationality_list">` + `<datalist>` — the browser filters as the parent types, no JS library. The input value is free text, so `validateNationality()` checks it against the list on blur and again at submit (case-insensitive, normalising `"ghana"` → `"Ghana"` in place); an unrecognised value shows an inline error and blocks submit with a jump to the Student section. Server-side stays the lenient `CharField` — same "the frontend check is a convenience, not the guarantee" stance as phone/DOB. Applies to both form copies (`backend/templates/public/apply.html` deployed, `frontend/application.html` preview). Inquiry doesn't collect nationality, so nothing changed there.

One real compatibility issue found in production data before shipping: the one existing value ever submitted (`"Ghanaian"`, a demonym, from before this field was constrained) doesn't match any option, which would silently leave the field blank when an in-progress draft holding that value is resumed. Fixed with a narrow `LEGACY_NATIONALITY_FIXUPS` map in `populateForm()` (currently just `{"Ghanaian": "Ghana"}`, the one value actually seen) — not a general demonym translator, just a compatibility shim for pre-constraint drafts.

### Academic year — school-year format

Both forms' academic year `<select>` options changed from a single year (`2026`) to a school year (`2027/2028`) — matches how this was originally scoped in Phase 1 (the single-year format shipped was a deviation, not the plan). Pure frontend change: `academic_year` is a free `CharField(max_length=50)` on both `Application` and `Capacity`, with no format constraint at the model or serializer level, so no migration was needed or run. Existing records (a handful of single-year and even a couple of ad hoc "Other" free-text values like `"2027 (mid-year transfer)"`) are left as-is rather than mechanically rewritten — some of those are genuine free-text the parent typed, not a value a script should reinterpret, and `Capacity` currently has zero rows so there's no live format-matching to conflict with yet.

### Submission latency — root cause and fix

Reported symptom: the Application form's final submit shows "Submitting…" for a long time even though the backend had already succeeded (real DB record, real confirmation email sent). Investigated with real evidence before changing anything, per two separate lines:

- **Production Cloud Run request logs** (`gcloud logging read ... httpRequest.latency`) showed the real submit endpoint taking 3.6s on a warm instance with no cold start nearby — and separately, plain draft-save `POST`s occasionally spiking to 5-6s, which turned out to be Cloud Run cold starts (`min-instances=0`), confirmed by the "Starting new instance... AUTOSCALING" log line appearing at the exact same timestamp, not an email-related slowdown.
- **Directly measured** `django.core.mail.get_connection().open()` against the real Resend SMTP relay: ~2.5-3s per connection, every time — because `emails.py`'s `_send()` opened a brand new SMTP+TLS connection for *every single email*, and a submission sends two (guardian confirmation + staff alert) sequentially. That's the real, confirmed root cause of the bulk of the multi-second wait — not a frontend bug (the fetch call, `response.ok` handling, and message display were all already correct).

Fix: `emails.py` gained `_shared_connection()`, a context manager that opens **one** SMTP connection and passes it into both `_send()` calls for a submission event, instead of each opening its own. Cuts the connection-setup cost from twice to once per event — confirmed by direct before/after timing (a 2-rejected-email test case dropped from 13.6s to 10.2s, a ~3.4s reduction matching almost exactly one avoided connection-open). Falls back to per-email connections if even the shared connection can't be opened, keeping the existing "email plumbing never blocks the real submission" guarantee.

This does **not** eliminate the latency — the response still waits on Resend synchronously, just for one connection's worth of overhead instead of two. True fire-and-forget dispatch (return success once the DB record exists, send email as a background step) would remove it entirely, but needs an actual queue (Cloud Tasks or similar) since Cloud Run only guarantees CPU during an active request by default — a spawned background thread can be frozen mid-send once the response returns. That's new GCP infrastructure with its own cost/complexity, flagged for the user to decide rather than added unasked.

Separately, `application.html` now shows "Still submitting — this can take up to 15 seconds…" if a submit takes more than 4 seconds, so the wait doesn't read as stuck.

### Draft-save failure after a successful submit — the other bug found in production logs

Real production log evidence: two `400`s on `PATCH .../application-drafts/<token>/` for a token that had already been successfully submitted 4-5 minutes earlier. Root cause: the free-navigation UI (sidebar nav, "Save & finish later") was only ever disabled by the success handler for `nextBtn`/`submitBtn`/`backBtn` — the sidebar items and save-later button stayed clickable after a successful submit, and clicking either autosaves via `saveDraft()`, which `PATCH`es the now-submitted draft and gets a real (correctly-behaving) `400 "This application has already been submitted"` — surfaced to the parent as a confusing generic "Could not save your progress" error, most plausibly by a parent who wasn't sure the slow submit (see above) had actually worked and clicked around afterward.

Fixed with `lockFormAsSubmitted()`, called on a successful submit, which disables the entire form UI (nav, both step buttons, save-later) — not just the three buttons it disabled before. As defense in depth (e.g. a stale second tab left open after submitting from another one), `saveDraft()` now also recognizes an `"already been submitted"` `400` specifically and treats it as success (locking the form) rather than reporting a save failure.

## Phase 6 — bulk/marketing email

### Schema

`Guardian` gained `bulk_email_unsubscribe_token` (unique, generated eagerly at creation — same pattern as `Offer.token`) and `bulk_email_unsubscribed_at` (null = subscribed; opt-out by default, not an explicit list). Checked only by the bulk-send recipient computation (`bulk_email.compute_recipient_rows()` and its Guardian/Lead pool helpers) — `emails.py` (every transactional send: confirmation, offer, draft-resume) has zero references to it, a deliberate hard separation rather than a runtime toggle someone could misconfigure. `Lead` (added 2026-09-01, see the "Lead capture" follow-up section below) carries the identically-named `bulk_email_unsubscribe_token`/`bulk_email_unsubscribed_at` fields so the same code path treats a Lead and a Guardian recipient interchangeably.

`EmailCampaign`: `subject`/`body` (plain text, `{{placeholder}}`-substituted at send time — see below), `audience` (`guardians` / `leads` / `both`, added 2026-09-01), optional `filter_stage`/`filter_academic_year`/`filter_campus` (Guardian audience only; blank = no filter on that dimension, AND semantics between set ones), optional `filter_lead_source` (Lead audience only), `status` (draft/queued/sending/sent/failed), `created_by`, and denormalized `total_recipients`/`sent_count`/`failed_count`/`skipped_count` counters. `clean()` refuses to validate without `{{unsubscribe_link}}` literally present in the body — a hard guardrail, not just documentation, confirmed by testing that `full_clean()` actually rejects a body missing it.

`EmailCampaignRecipient`: one row per (campaign, recipient) — the audit trail. Exactly one of `guardian` / `lead` is set (both nullable FKs since 2026-09-01, enforced by a DB `CheckConstraint`; the old `unique_together = (campaign, guardian)` became two partial `UniqueConstraint`s, one per target type). `email` is a snapshot at send time (the address could change later); `status` (pending/sent/failed/skipped_unsubscribed/skipped_invalid); `resend_message_id` (from the batch API's own response, kept for a possible future bounce-webhook correlation even though bounce tracking itself isn't built); `error_message`. Recipients are computed **once**, when staff click Send — not recalculated afterward, so this stays an accurate historical record even if Guardian/Lead data changes later.

New permission `admissions.can_send_bulk_email` (on `EmailCampaign`), not auto-granted — same deliberate-grant treatment `can_view_health_info` got. Drafting and Preview need only the normal model permissions Django creates automatically; only the Send action itself is gated (`EmailCampaignAdmin.get_actions()`, same pattern as `can_decide` hiding `generate_offer`/`reset_offer`).

**A real migration bug caught and fixed before it reached anyone:** adding `bulk_email_unsubscribe_token` as a `unique=True` field with a callable default to a table with existing rows triggers Django's own interactive "won't generate unique values" warning — sidestepped by making it nullable first, but that alone wasn't enough: Django's `AddField` computes a *callable* default **once** and applies that single value to every existing row via one UPDATE, not per-row (a known but easy-to-miss Django gotcha). Confirmed directly against the real data: all 26 existing Guardians got the identical token. Fixed by reversing the data migration and rewriting it to unconditionally assign a fresh token to every row (not filtered by `isnull`, since none were actually null), then re-verified zero duplicates before re-applying the final `unique=True` migration.

### Template placeholders

`bulk_email.render_template()` — a whitelisted `{{name}}` regex substitution, not Django's template engine, so a staff-authored subject/body can't execute arbitrary `{% %}` template logic (a mail-merge doesn't need that). Available: `{{recipient_first_name}}`, `{{recipient_full_name}}` (added 2026-09-01, resolve for a Guardian *or* a Lead recipient — prefer these), `{{guardian_first_name}}`, `{{guardian_full_name}}` (kept as back-compat aliases; for a Lead they resolve to the same first-token / full `name`), `{{student_names}}` (comma-joined across a family's children; falls back to "your child" for a Lead, which has none), `{{unsubscribe_link}}` (required). An unknown placeholder is left as literal text rather than silently blanked, so a typo is visible in Preview instead of vanishing.

### Sending mechanism — Resend's batch API, not SMTP

Every other email in this system (`emails.py`) sends via SMTP, one connection per message. At TCS's real family count (500-2,000) that's infeasible against Resend's confirmed **10 requests/second per-team** rate limit — minutes of serial sends, not viable synchronously or as a tight loop. Bulk email instead uses Resend's HTTP batch endpoint (`https://api.resend.com/emails/batch`, up to 100 personalized emails per call — confirmed empirically, not assumed from docs) via raw `urllib`, same low-dependency pattern as `storage.py`'s Supabase calls; `RESEND_API_KEY` doubles as the HTTP Bearer token here, same credential already used as the SMTP password.

Confirmed directly (real test calls against Resend, using their own safe `*@resend.dev` test addresses) that the batch endpoint is **all-or-nothing at the HTTP level** — either every email in the request is accepted (200, one `{"id": ...}` per input item, same order as the request) or the whole call fails (e.g. 403 for an unverified domain). There's no partial per-item success/failure shape to handle, which meaningfully simplified the retry design below.

**A real bug found only by actually sending a real batch, not by reading Resend's docs:** the first real attempt failed with a `403` and Cloudflare's own `"error code: 1010"` body — not a Resend error at all. `urllib`'s default `User-Agent` ("Python-urllib/3.x") gets blocked outright by Cloudflare (fronting `api.resend.com`) as bot traffic. `storage.py`'s Supabase calls happen not to hit this (different Cloudflare config on their side) — fixed by setting an explicit `User-Agent` header on the Resend batch request specifically.

### Pre-send address validation — the batch is atomic, so one bad address can't fail the rest

Because Resend's batch endpoint is all-or-nothing (see above), one recipient with an obviously bad address used to fail the *entire* batch it was in — confirmed twice independently: once by accident (campaign 4's real first send included an `@example.com` guardian address and all 11 recipients in that batch came back `failed`), then deliberately reproduced by the user (2 bad addresses among 7 → all 7 failed).

Fixed with a validation pass in `bulk_email.enqueue_campaign_send()`, immediately before pending recipients are split into batches — deliberately not at campaign creation, since a draft can sit for a while before Send and Guardian data can change in between, and `enqueue_campaign_send` is the one function every send path (`send_campaign`, `retry_failed_recipients`) already funnels through, so it's the single point that guarantees a bad address can never reach Resend regardless of which action queued it. `bulk_email.invalid_email_reason()` checks basic format via Django's own `EmailValidator`, plus a small explicit blocklist of placeholder domains (`example.com`/`.org`/`.net`/`.edu`, `test.com`) — justified directly by Resend's own rejection message naming "domains like `example.com`", not guessed. Deliberately narrow: this catches obviously malformed/placeholder addresses, not real deliverability — a syntactically valid address at a domain that doesn't accept mail still bounces *after* sending, which needs bounce-webhook handling and stays out of scope here. Rejected rows are marked `skipped_invalid` immediately and never see a Cloud Task at all — confirmed via a real mixed batch (4 valid `delivered@resend.dev` recipients + 1 malformed address) that the invalid one was flagged before any Resend call while the 4 valid ones sent successfully, i.e. the batch no longer fails as a whole.

`EmailCampaign.skipped_count` and the "recompute counts, finalize once nothing's left pending" logic (`bulk_email.finalize_campaign()`, shared by `BulkEmailBatchSendView` and the all-invalid edge case in `enqueue_campaign_send` where no Cloud Task ever gets created) track this alongside `sent_count`/`failed_count`.

### Cloud Tasks — the background-job mechanism

No task queue existed in this project before this (no Celery, no cron — same "nothing exists yet" starting point `OFFER_EXPIRY_DAYS`'s comment already noted). Flow: staff clicks Send → `EmailCampaignRecipient` rows created synchronously (fast, just DB writes) → one Cloud Task enqueued per batch of ≤100 recipients (~20 tasks for 2,000 people) → each task POSTs to `BulkEmailBatchSendView`, an internal endpoint (shared-secret header, `hmac.compare_digest` — never `==` — against `BULK_EMAIL_INTERNAL_SECRET`; refuses everything if that secret is unset, so an accidentally-empty production value can never open the endpoint to `compare_digest("", "")` matching an empty header).

The queue itself is rate-limited (`--max-dispatches-per-second=5`, a real GCP queue setting — see `docs/deployment.md` step 5c) — this is the actual throttle against Resend's limit, not hand-rolled pacing in application code.

**Retry safety, confirmed by directly testing both branches, not just written and assumed correct:** Cloud Tasks sends `X-CloudTasks-TaskRetryCount` on a redispatch. On a transient failure with retries remaining, recipients are left `pending` (not marked failed) and the view returns a non-2xx status so Cloud Tasks retries the same batch — confirmed via `curl` that a forced Resend failure at `retry_count=0` leaves rows `pending`. Only once `retry_count` reaches `CLOUD_TASKS_MAX_ATTEMPTS - 1` (the last allowed attempt) are recipients marked terminally `failed` — confirmed via a forced failure at `retry_count=2`. On success, a *repeated* dispatch of the same batch (simulating Cloud Tasks retrying after our own response was lost in transit despite succeeding) correctly reports `processed: 0` and touches nothing, since the handler only ever operates on rows still `status="pending"`.

**A real robustness bug found and fixed during testing:** the enqueue step (`bulk_email.enqueue_campaign_send()`) can fail (confirmed directly — it raises `DefaultCredentialsError` with no GCP credentials configured, the exact failure mode a real misconfiguration would produce) — the original code let this propagate as an unhandled 500 *after* already creating the campaign's recipient rows and setting `status="queued"`, leaving a permanently stuck campaign with rows created but nothing ever dispatched. Fixed: the recipient-creation + status-update happens inside a DB transaction, and the enqueue call is wrapped separately — a failure there deletes the just-created recipient rows and reverts the campaign to `draft`, so a retry (once whatever broke is fixed) starts clean rather than needing manual DB surgery.

Real Cloud Tasks integration confirmed end-to-end where possible from this environment: actual task creation against the real queue (via a temporary, immediately-revoked service account key — no long-lived credential left behind) succeeded; actual dispatch to the handler was simulated with a direct request carrying the same headers Cloud Tasks sends, since Cloud Tasks itself can't reach a local dev server's loopback address — that part can only be confirmed once this is deployed.

### Sender reputation — a dedicated subdomain

`BULK_EMAIL_FROM_EMAIL` defaults to `updates@updates.tcsch.edu.gh` — a separate subdomain from `DEFAULT_FROM_EMAIL`'s `tcsch.edu.gh`, verified as its own domain in Resend (`docs/deployment.md` step 6b). Bulk/marketing mail is far more likely to generate spam complaints or a high bounce rate than a one-off transactional confirmation; a shared domain would let that damage the deliverability of the offer/confirmation emails that actually matter. This is real DNS/Resend setup work still needed from the user — not something done as part of this build, unlike the GCP-side Cloud Tasks setup.

### Unsubscribe flow

One view (`UnsubscribeView`), token-only access control (same trust model as `Offer`/`ApplicationDraft`) — `GET` renders a human-facing confirmation page (`templates/public/unsubscribed.html`, no `frontend/*.html` counterpart needed since there's no interactive JS to preview outside Django, unlike the three form pages); `POST` is RFC 8058's one-click unsubscribe, which Gmail/Outlook fire silently server-to-server with no rendered response. Both set `Guardian.bulk_email_unsubscribed_at` idempotently.

**A real bug found and fixed during testing:** the one-click `POST` failed with a real `403 CSRF verification failed` — `UnsubscribeView` is a plain Django `View`, not a DRF `APIView`, so it never got the automatic `csrf_exempt` wrapping DRF's `APIView.as_view()` applies (see the CSRF investigation elsewhere in this doc for why that automatic exemption exists at all). A mail provider's one-click POST has no browser session or CSRF cookie by design — RFC 8058 requires exactly that. Fixed with an explicit `@method_decorator(csrf_exempt, name="dispatch")`; safe here since unsubscribing is idempotent/low-stakes and the token itself is the real access control.

Confirmed end-to-end with real tokens: `GET` renders the correct guardian name and the transactional-email carve-out message and sets `bulk_email_unsubscribed_at`; a repeated `GET` or `POST` doesn't reset the original timestamp; `POST` returns a real `200` with an empty body (correct RFC 8058 shape); a campaign's recipient computation correctly excludes both test guardians once unsubscribed. (Since 2026-09-01 `UnsubscribeView` also resolves a token against `Lead` — see below.)

## Phase 6 follow-up — Lead capture (enquiries before application) (2026-09-01)

Built the long-deferred Phase 6 "enquiries before application" scope, to a plan the user approved point by point (field names, throttle rate, consent defaults, audience-targeting UX, token naming, CORS) before any code. Deployed to production 2026-09-02 (revision `admissions-00018`); a wrong-school-name string in the PDF-gate email was fixed the next day (`admissions-00019`). Full test coverage lives in `admissions/tests.py` — the project's first real test module (25 tests, run on in-memory SQLite since Supabase's pooler can't `CREATE DATABASE` for Django's default test runner: `DATABASE_URL=sqlite://:memory: python manage.py test admissions`).

### `Lead` model

Deliberately flat — no Family/Guardian/Student/Application, no stage, no workflow. It's a queryable safety net plus a bulk-email audience; staff who want to actually progress a prospect re-key them through the normal Inquiry form.

| Field | Notes |
|---|---|
| `name` | single free-text field (no first/surname split — a `first_name` *property* returns the first token for `{{recipient_first_name}}`) |
| `email` / `phone` | both `blank`; **at least one required**, enforced in the serializers (the only write paths), not the model |
| `grade_interest` | free text, optional |
| `source` | choices `quick_interest_widget` / `pdf_gate_admissions_overview` — **set server-side per endpoint, never read from the client** |
| `consent_to_marketing` | `BooleanField(default=False)` — see the consent note below |
| `bulk_email_unsubscribe_token` / `bulk_email_unsubscribed_at` | identical pattern and naming to `Guardian`, so `bulk_email.py` and `UnsubscribeView` treat the two interchangeably |
| `created_at` | |

Admin: a plain list (`name`, contact, `grade_interest`, `source`, `consent_to_marketing`, unsubscribed-at, `created_at`), filterable by source and consent. `has_add_permission` returns `False` — leads only ever arrive via the two endpoints.

### Public endpoints

Both `AllowAny` + `TurnstileProtectedCreateMixin` (same bot-protection gate as Inquiry/Application/Offer) + a new `LeadRateThrottle` (`lead_capture` scope, **60/hour** — higher than the default `anon` 20/hour because the marketing site shares office/NAT IPs; same precedent as `application_draft`). snake_case bodies, `201` + JSON on success, `400` + field errors on failure — the house pattern. CORS-reachable from the marketing site (see below).

- **`POST /api/admissions/quick-interest/`** — `QuickInterestSerializer`. `name` + (`email` or `phone`; `phone` gets the same `+CCCXXXXXXXXX` regex as every other form) + optional `grade_interest` + optional `consent_to_marketing`. Fires a staff-notification email only; nothing is sent to the lead.
- **`POST /api/admissions/pdf-gate/admissions-overview/`** — `PdfGateSerializer` (subclass; `email` is **required** here because the document is emailed to it). Emails the "Admissions Overview & Fees" PDF via the existing generic attachment mechanism — new `settings.PDF_GATE_ATTACHMENTS` (default `["admissions-overview-and-fees.pdf"]`, resolved in `ADMISSIONS_ATTACHMENTS_DIR`). The real 4.9 MB file was committed (`9505352`) and baked into the image; a missing file is still logged-and-skipped with the lead captured anyway, same as `INQUIRY_EMAIL_ATTACHMENTS`. Also fires a staff notification.

**Consent safety net:** the `consent_to_marketing` serializer field is `required=False, default=False` on both endpoints — an omitted or blank value is always `False` regardless of what the form sends; only an explicit `true` (a genuinely ticked box) is honoured. The backend cannot distinguish a deliberate `true` from a pre-checked-by-default box, so the default is the guarantee — the marketing site must ship the checkbox unticked.

### Bulk email — Lead audience

`EmailCampaign.audience` (`guardians` default / `leads` / `both`, required) + `filter_lead_source`. `bulk_email.compute_recipient_rows()` replaces the old `compute_recipients()` + manual guardian loop in `send_campaign`: it builds unsaved `EmailCampaignRecipient` rows from a **Guardian pool** (unchanged `filter_*` logic, only when audience includes guardians) and a **Lead pool** (`consent_to_marketing=True`, not unsubscribed, optional `filter_lead_source` — the Guardian stage/year/campus filters never touch leads), then **de-dupes by lowercased email with the Guardian row winning** on a collision (its template context — real child names — is strictly richer). Preview renders one sample per audience type in play (two, for `both`). `retry_failed_recipients` re-pulls `email` from the Guardian *or* Lead. `UnsubscribeView` resolves a token against `Guardian` then `Lead` (they share one 32-random-byte token space; a cross-table collision is astronomically unlikely).

### CORS

`settings.CORS_ALLOWED_ORIGINS` now defaults to `https://tcsch.edu.gh` + `https://www.tcsch.edu.gh` — the **separate marketing site** whose widgets POST here cross-origin. The admissions app's own forms are same-origin and need no entry. New `settings.CORS_ALLOWED_ORIGIN_REGEXES`, default `^https://[a-z0-9-]+\.vercel\.app$`, covers the marketing site's Vercel preview deployments. Both are env-overridable; the live Cloud Run service sets `CORS_ALLOWED_ORIGINS` as an env var (which overrides the code default entirely), so `docs/deployment.md` step 7 sets both there using gcloud's `^##^` alternate-delimiter form. Verified live 2026-09-02: preflight from `tcsch.edu.gh` and a `*.vercel.app` host both echo `access-control-allow-origin`; an unlisted origin gets none.

## Transactional email — off the request cycle (b2, 2026-09-03)

The Inquiry / Application / draft-resume confirmation + staff-alert emails used
to be sent synchronously inside the submission's `perform_create` — with a
shared SMTP connection (Phase 5 fix) that roughly halved it, but the response
still waited ~3 s on Resend. They now go through Cloud Tasks, reusing the
Phase 6 infrastructure.

**Flow.** The view still calls `emails.send_inquiry_emails` /
`send_application_emails` / `send_draft_resume_email` — unchanged names,
unchanged signatures, so the views themselves didn't change. Those now:
build each message (identical text), `bulk_create` a `TransactionalEmail` row
per message (`status="pending"`), and enqueue **one** Cloud Task carrying the
row ids to `POST /api/admissions/internal/send-transactional-email/`. The view
returns `201` immediately — **response contract unchanged**, just faster; the
email lands a few seconds later.

**`TransactionalEmail` model** (migration `0015`): `kind`
(inquiry_parent/inquiry_staff/application_parent/application_staff/draft_resume),
`to_email`, `subject`, `body` (rendered snapshots), `attachments` (filenames
only — resolved against `ADMISSIONS_ATTACHMENTS_DIR` at send time, never
bytes, which wouldn't fit a task payload), `status`
(pending/sent/failed), `attempts`, `last_error`, nullable `application` FK,
`created_at`/`sent_at`. `TransactionalEmailAdmin` is view-only with a
`resend_failed` action (re-queues `failed` rows; falls back to inline send if
the queue is down).

**Worker** (`TransactionalEmailSendView`): shared-secret header auth via the
new `admissions/internal_auth.internal_secret_ok` (extracted from
`BulkEmailBatchSendView`, which now uses it too) — **reuses
`BULK_EMAIL_INTERNAL_SECRET`**, same trust boundary. Only ever operates on
rows still `pending`, so a Cloud Tasks redelivery is a no-op (`processed: 0`).
Per-row: on failure bump `attempts`; mark `failed` + store `last_error` only
on the last allowed attempt (`X-CloudTasks-TaskRetryCount >=
CLOUD_TASKS_TRANSACTIONAL_MAX_ATTEMPTS - 1`), otherwise leave `pending` and
502 so Cloud Tasks retries the batch. Same retry-safety shape as
`BulkEmailBatchSendView`.

**Dedicated queue** `admissions-transactional-email` (`deployment.md` step 5d),
not the bulk queue — a confirmation email must not queue behind a draining
2,000-recipient campaign. `--max-dispatches-per-second=2`, `--max-attempts=5`.

**Fallback / dev / tests.** `enqueue_transactional_ids` fast-fails with a
`RuntimeError` when `GCP_PROJECT_ID` is unset (before building any gRPC
client), and `enqueue_submission_emails` catches *any* enqueue failure and
**sends the rows inline right then**. So: no queue configured, no GCP creds, a
transient Cloud Tasks error → behaviour is exactly the old synchronous path,
logged. Local dev and the test suite need zero Cloud Tasks setup.

**Scope.** Deliberately Inquiry / Application / draft-resume only — the public,
parent-is-waiting paths. `send_offer_email` (staff clicks "Generate Offer" in
admin) and the Lead-capture emails stay synchronous: staff-initiated or
already off the critical path, and routing an admin action through a queue
would add a failure mode to a workflow that currently gives immediate
feedback. They share `_deliver()`, so migrating them later is a small change.

## Admissions-specific roles (built on the shared RBAC pattern)

- Admissions Officer, Reviewer, Admin — Django Groups scoped to the `admissions` app's models only.
- `admissions.can_view_health_info` (Phase 5) — ungranted by default; who gets it is a decision for the user, not auto-assigned.
- `admissions.can_send_bulk_email` (Phase 6) — ungranted by default, same reasoning.

## Open questions to resolve before/during Phase 1

- Exact required documents per year group (Preschool through JHS may differ)
- Who are the actual admissions staff roles at TCS right now, and what should each see?
- ~~Application fee: does TCS charge one?~~ Resolved in Phase 5 — yes, GHS 200, offline bank transfer/mobile money, proof uploaded as a Document.
