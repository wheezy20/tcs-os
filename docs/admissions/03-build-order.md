# TCS Admissions Module — Build Order

**Status:** active work plan. Update this file as phases complete, scope shifts, or new phases get defined. Mark a phase `DONE` (with date) instead of deleting it, so the history stays visible.

**Current phase: none active — Phases 1–6 DONE, plus post-Phase-6 follow-ups (a7 inquiry PDF attachment, b3 nationality typeahead, b2 async transactional email — all 2026-09-03, `admissions/tests.py` at 39 tests). Next up is either a scoped Phase 6.1 (bounce/open tracking, topic-level opt-outs) or Phase 7+ from `01-vision.md`. Several operational go-live items remain, and b2 needs deploying — see `docs/deployment.md`'s "Current deployment state".**

---

## Phase 1 — Working inquiry pipeline — DONE (2026-08-18)

Scope shifted from the original plan below in a few real ways — noted inline rather than silently.

**What shipped:**
- Django app `admissions`, connected to the shared Supabase Postgres
- Models: `Family`, `Guardian`, `Student`, `Application`, `Document`, `Note`
- Django admin registered (shared `django-unfold` theme)
- `POST /api/admissions/inquiries/`
- Public inquiry form: a standalone, dependency-free HTML/JS page (`frontend/index.html`) rather than Lovable-generated — tested locally against a real Supabase project, **not yet deployed** to `admissions.tcsch.edu.gh`

**Follow-up within the same phase** (a later session, same banner): the form was rebuilt to match TCS's actual paper enquiry form — 4 sections (referral source; 1-2 parents/guardians; 1-5 children, each with their own grade/academic year/month of enrollment; comments). `Guardian.full_name` was split into `first_name`/`surname` via a migration with a data-preserving backfill (existing rows kept their names, nothing was lost).

**Moved out of Phase 1:** file upload (frontend → Supabase Storage → Django) — the original plan bundled this into Phase 1, but Inquiry never actually collects documents; only Application does. It shipped as part of Phase 2 instead.

**Still not done, no phase currently owns it:** Cloudflare (subdomain proxy, Turnstile on the form, rate-limit rule on `/api/admissions/*`). Nothing has been deployed anywhere yet — everything has been tested against local `runserver`/`gunicorn` and the real Supabase project, not a live URL. **Resolved (2026-08-26):** Turnstile shipped — see the Phase 5 follow-up entry below and `04-build-log.md`. The Cloudflare rate-limit rule is dashboard config, not code — steps given in `docs/deployment.md`, not yet applied by the user.

**Definition of done, revised:** a parent can submit a full inquiry (multiple children, multiple guardians) and it's correctly modeled in Postgres; staff can see and work it in the branded admin. Met. Not met: actually live on the real subdomain, behind Cloudflare.

---

## Phase 2 — Application form, documents, notifications — DONE (2026-08-18)

The original plan below assumed Phase 2 would be a review-workflow layer added on top of an already-deployed Phase 1. What actually got scoped and built instead, once Phase 1 was functionally complete but still local-only:

**What shipped:**
- Public Application form (`frontend/application.html`) — a **separate entry point** from Inquiry; a parent can apply directly without ever submitting an Inquiry first
- Dedup/matching: reuses an existing `Family`/`Guardian`/`Student` by guardian email + student name/DOB instead of creating duplicates; advances an existing Inquiry-stage `Application` to `stage="application"` instead of creating a second row for the same student/year/grade
- Document uploads via signed Supabase Storage upload URLs — the browser uploads directly to Supabase, the service role key never leaves the Django server
- Proof of vaccination conditionally required for preschool-grade applicants, enforced both client-side and in `ApplicationSerializer.validate()`
- Plain-text confirmation + internal staff-alert emails, one pair per submission event (both Inquiry and Application) — narrower than the original plan's "automated email on stage change" below, which implied every stage transition, not just the initial submission
- Admin: bulk stage-transition actions; fixed `Note.author` never being set on new inline notes (was `readonly_fields` with nothing assigning it)

**Deferred from the original Phase 2 plan below — not built, not currently scheduled:**
- Kanban-style or dedicated staff review view (current UI is plain Django admin list/actions)
- Reviewer tracking on document approve/reject (the resulting `status` is captured, not who made the call)
- Basic audit log (who changed what, when)
- Simple applications-by-stage dashboard

Revisit these if/when actually needed — nothing in Phase 3 depends on them.

---

## Phase 2.5 — Reference ID numbering — DONE (2026-08-18)

Deferred out of Phase 2 mid-session (this used to be a standalone note living directly in `04-build-log.md`; folded in here properly now that it's shipped, rather than staying an orphaned note with no phase of its own).

- Three numbering schemes: Inquiry reference (`INQ-YYYY-NNNN`), Application reference (`APP-YYYY-NNNN`), permanent Student ID (`YYPPNNNN`) — full format/assignment-trigger detail in `02-stack-and-schema.md`
- `ReferenceCounter` model, incremented under `select_for_update()` so concurrent submissions can't be handed the same number — verified against real concurrent OS processes hitting the real Supabase Postgres backend, no duplicates or gaps
- Student ID classification has no code for SHS (Grade 10-12) since TCS doesn't offer it yet — a student enrolled at that level gets no ID assigned (never a guessed code) and a logged warning, so it surfaces as a visibly-missing field rather than silently wrong data
- `reset_admissions_data` management command built (truncates all admissions tables, resets ID sequences, requires typing "yes" to confirm) — committed (`acb4589`), **built but deliberately never run**; exists for wiping test data immediately before go-live

**Settled (2026-09-03):** TCS confirmed there is no pre-existing paper/legacy student-numbering convention to reconcile against — the three formats (`INQ-YYYY-NNNN`, `APP-YYYY-NNNN`, `YYPPNNNN`) are final. See `02-stack-and-schema.md`.

---

## Phase 3 — Decisions, offers, enrolment — DONE (2026-08-18)

**What shipped:**
- `Decision` model (accepted/waitlisted/rejected), mutable one-per-Application, gated behind a new `admissions.can_decide` permission — a plain Django Group/Permission, not a new role/profile system (see `02-stack-and-schema.md`)
- `Offer` model — token-based, emailed to the parent (`frontend/offer.html`, `POST /api/admissions/offers/<token>/respond/`), 14-day expiry resolved lazily on read (no scheduler exists in this project yet)
- Real gating: `Application.save()` blocks entering `offer` (needs an accepted Decision) and `enrolled` (needs an accepted Decision *and* an accepted Offer) via one shared mechanism — this fully closes the gap the Phase 2.5 stopgap only partially closed, and it's enforced in the model, not just in an admin action, so it holds regardless of entry point
- New `Application.STAGE_CHOICES`: `waitlisted`, `rejected`, `offer_declined` — negative Decision/Offer outcomes propagate onto `Application.stage` automatically (a declined or expired Offer, or a waitlisted/rejected Decision, updates the stage without a separate staff step); positive outcomes never auto-advance, they only unlock the next gate for a deliberate staff action
- `Capacity` model (seats per academic_year/year_group) — `ApplicationAdmin` shows a soft `messages.WARNING` when saving a Decision as "accepted" would put the accepted count for that (year, grade) over capacity; never blocks the save. Built as a follow-up (see below), not in the original Phase 3 pass
- Waitlist promotion is a manual staff action (change `Decision.decision_type` from `waitlisted` to `accepted`, then Generate Offer) — no automated promotion, deliberately, so a human always makes the call on who gets an opening

**A real bug found and fixed during testing:** `Decision.save()`/`Offer.save()`'s stage-propagation logic originally trusted `self.application.stage`, but that can be a stale, already-mutated-in-memory value when the save is triggered from inside `Application.save()`'s own gate check (exactly what happens when an admin bulk action sets `.stage` before calling `.save()`). Fixed by always re-fetching the `Application` row fresh rather than trusting a possibly-cached instance — full detail in `02-stack-and-schema.md`.

**Follow-up within the same phase** (a later session): the capacity soft-warning was actually built (it had only been storage before, despite an initially-misleading docstring caught during a documentation-accuracy pass), and waitlist promotion was tested end to end for the first time — Decision→waitlisted, changed to accepted, Generate Offer succeeds from the `waitlisted` stage, and the rest of the chain (parent accepts, Mark Enrolled) proceeds normally. Both previously-open items from this phase are now closed.

## Phase 4 — Branding & emailed collateral — DONE (2026-08-19)

**Not a newly-invented requirement.** The original Phase 1 kickoff said "no TCS branding yet (Phase 2)," implying branding was expected to land in Phase 2 — but Phase 2, as it actually got built, never touched it, and no later phase picked it up either. Surfaced during a documentation audit; this was that planning gap being closed, not new scope.

**What shipped**, following the full brand guide in `docs/admissions/brand-tokens.md` precisely (not placeholder colors/fonts):
- Admin dashboard fully rebranded: `django-unfold` `SITE_LOGO`/`SITE_ICON`/`SITE_FAVICONS` swap the correct logo variant per the brand guide's light/dark rule (mint-leaf laurel on white, lime laurel on dark) — via callables in `admissions/branding.py`, not hardcoded path strings, since whitenoise's manifest storage needs `static()` resolved at request time, not settings-import time. `SITE_TITLE`/`SITE_HEADER` now read "Treasures Christian School." `COLORS.primary` is an 11-stop ramp interpolated between actual brand colors (Jungle Mist → Deep Jungle Green → Deep Teal Shadow → near-black), not invented. A custom `templates/admin/login.html` overrides (extends, doesn't replace) unfold's own login template to add the vertical logo, correctly swapping mint-leaf/white-bg for light theme and lime/teal-bg for dark theme.
- All three public forms (`index.html`, `application.html`, `offer.html`) rebranded identically: horizontal logo header, Cinzel for headings (`h1`, `legend`), DM Sans for body/labels/inputs/buttons (Google Fonts `<link>`, no build step, dependency-free pattern preserved), full brand color palette replacing the old generic blue.
- Generic file-attachment support added to `admissions/emails.py` (`_send()` now uses `EmailMessage` instead of `send_mail()` for attachment support) — a directory (`ADMISSIONS_ATTACHMENTS_DIR`) plus a per-email-type filename list, not hardcoded to any one document. A missing configured file is logged and skipped, never blocks the send. Wired into the Inquiry parent-confirmation email as the flagship use; extending to other email types is a one-line change. *(2026-09-03: `INQUIRY_EMAIL_ATTACHMENTS` default set to `["admissions-overview-and-fees.pdf"]` — reuses the file already shipped for the PDF-gate download, so the inquiry confirmation now carries the Admissions Overview & Fees PDF.)*

**A real deployment risk caught and avoided, not just discovered after the fact:** the plan was to point Django's `STATICFILES_DIRS` at the existing `frontend/assets/` directory to avoid duplicating the logo files. Caught before implementing: the Dockerfile's `collectstatic` runs at build time inside a `backend/`-scoped build context, which may not include the sibling `frontend/` directory depending on how the image is built — a `STATICFILES_DIRS` entry pointing outside that context would silently work in dev and break on first real deploy. Copied the 6 logo files into `admissions/static/admissions/branding/` instead (Django's standard per-app static convention, guaranteed inside the build context) — at the cost of the assets existing in two places (`frontend/assets/branding/` for the standalone HTML forms, `backend/admissions/static/admissions/branding/` for Django) that need manual sync if the brand assets are ever updated.

## Phase 4.5 — Deployment prep — DONE (2026-08-19)

Closes out the "still not done" item flagged back in Phase 1 (Cloudflare, real subdomain) — this is that go-live work, not new admissions functionality.

**What shipped:**
- Routing: `/inquiry`, `/apply`, `/offer` now served directly by Django (`tcs_os/urls.py`, plain `TemplateView`s), so the whole module — API, admin, and public forms — lives under one subdomain with no separate static host or cross-origin surface. `/` redirects to `/inquiry` (the actual top-of-funnel entry point). Templates live in `backend/templates/public/`, adapted copies of `frontend/*.html` — asset paths go through `{% static %}` (reusing the branding files already duplicated into `admissions/static/` for Phase 4, no third copy) and the API base URL is root-relative (`/api/admissions/...`) instead of hardcoded to `127.0.0.1:8000`. `frontend/*.html` are left as-is, still useful for a quick local preview outside Django — see the tradeoff note in `04-build-log.md`.
- `emails.py`'s offer link fixed from `/offer.html?token=` to `/offer?token=`, matching the new route (no `.html`).
- Dockerfile hardened for production, verified with real `docker build` + `docker run`, not just read-through: added a non-root user (a real bug was hit and fixed while doing this — see build log), dropped an unnecessary `build-essential`/`libpq-dev` install (requirements.txt only uses `psycopg2-binary`, a prebuilt wheel), and fixed a real build-breaking bug found in the *existing* Dockerfile (`collectstatic` needs `SECRET_KEY`/`DATABASE_URL` just to import settings, and neither had a build-time value — the image never actually built before this).
- Resend wired up as `EMAIL_BACKEND` via Django's built-in SMTP backend (Resend's SMTP relay, not `django-anymail` — no new dependency; confirmed against Resend's own docs that SMTP is directly supported, not assumed). Gated behind `RESEND_API_KEY`: unset falls back to the console backend exactly as before, so local dev is unaffected.
- `docs/deployment.md` — full `gcloud` command sequence for the first real deploy (Secret Manager, Artifact Registry, Cloud Run service + Jobs for migrate/createsuperuser, domain mapping). Not run — written for you to execute.

**Not done here, deliberately out of scope:** Turnstile on the forms, the Cloudflare rate-limit rule on `/api/admissions/*` — both flagged as open since Phase 1, still open. **Resolved (2026-08-26):** see the Phase 5 follow-up entry below.

## Phase 5 — Expanded Application form — DONE (2026-08-26, plus three same-week follow-ups: free navigation `31f5093`, upload constraints `08c9dc9`, Turnstile + loose ends `63e00d2`)

Brings the Application form up to the school's real paper/Google Forms process — full plan reviewed and approved before any code was written (schema, draft/resume design, and the Campus×Capacity question all put to the user explicitly; see `04-build-log.md` for the resolutions).

**What shipped:**
- `Student`: gender, nationality, its own address block (address/town_city/postal_code/country — previously only Guardian had one), previous_school_location (distinct from `current_school`, which is the school's name)
- New `Campus` model (Main, Annex — seeded via data migration) and new `EmergencyContact`/`HealthInfo` models, both Application-scoped (not Student- or Family-scoped) so they sit as inlines on the same `ApplicationAdmin` page as everything else
- `HealthInfo` access restricted behind a new `admissions.can_view_health_info` permission — real child health data, not visible to every Admissions Officer by default (nobody has this permission yet; granting it to a role is a deliberate decision left to the user, not auto-assigned)
- `Capacity` now scoped by campus too (`academic_year`, `year_group`, `campus`) — TCS's two campuses are physically separate seat pools; verified independently (a Main grade hitting its own cap doesn't warn against Annex's separate cap for the same grade/year)
- Annex campus grade restriction (Pre Nursery/Nursery 1 only) enforced the same way the existing preschool-vaccination rule already is — a hardcoded grade set checked in `ApplicationSerializer.validate()`, not a new pattern
- Application fee (GHS 200, offline bank transfer/mobile money): reuses the existing `Document` model entirely — a new `application_fee_proof` type, same upload/review workflow as every other document. Payment instructions are an env-var-driven setting (`APPLICATION_FEE_PAYMENT_INSTRUCTIONS`), included in the confirmation email; the on-page copy is static HTML (this project's forms are dependency-free by design), so updating bank details later means editing both places
- Declaration: two independent consents, not one — `declaration_agreed` (indemnity/data-protection/accuracy, required) and `media_consent_agreed` (separate, optional, independently revocable per its own text) — plus a lightweight audit trail (`declaration_agreed_at`, `declaration_ip_address`, best-effort per REMOTE_ADDR's own caveats behind a proxy)
- `ApplicationDraft` — token-based save/resume, same trust model and token generator as `Offer`'s existing resume link. Raw JSON, not partial real rows (a draft can be genuinely incomplete/invalid at any point); full validation only ever happens once, at final submit, through the same `ApplicationSerializer` a direct submission uses
- `application.html` rebuilt as a 6-step form (Guardian(s) → Student → Emergency Contact → Health/Wellbeing → Documents & Payment → Declaration) with a progress bar, autosaving to the draft on every "Next," and an explicit "Save & finish later" action that emails a resume link — separate from autosave so it doesn't spam an inbox
- Tested end-to-end for real, not by inspection: a full 6-step submission through the actual browser (including two real Supabase document uploads) verified correct in the database afterward; a separate save-for-later → real resend email → resume-on-a-fresh-page test confirmed all fields (including the reverse dd/mm/yyyy conversion) restore correctly; server-side rejection confirmed for Annex+wrong-grade, an unknown campus name, and a missing declaration; the health-info permission gate confirmed both ways in the real admin UI, not just by code reading

**Not done here, deliberately out of scope:** a real payment portal (stays offline/proof-upload until one exists) and a real parent login/portal — the draft token is an explicit stopgap for the latter, flagged in `01-vision.md`, not a replacement for one.

### Phase 5 follow-up — free navigation, loose ends (2026-08-26)

Two later sessions on top of the base Phase 5 build, same phase — not new functionality so much as production hardening and loose ends.

- Replaced strictly-linear Next/Back with free jump-between-sections (sidebar on desktop, horizontal scroll bar on mobile) — see `04-build-log.md` for the two real bugs found while testing it (a throttle scope shared with real submissions, and a mobile CSS width-vs-height bug).
- Document upload type/size limits (PDF/JPG/PNG, 10MB), enforced client-side, server-side, and on the Supabase bucket itself — the bucket-level limit is the one that can't be bypassed by a client lying about its own file size.
- **Turnstile + Cloudflare rate-limit rule** — the item flagged as open since Phase 1 (see above). Turnstile shipped on all three public forms (Inquiry, Application's final submit, Offer accept/decline); the rate-limit rule is Cloudflare dashboard config, not code — see `docs/deployment.md`.
- Nationality changed from free text to a dropdown (ISO 3166-1 country list) — was previously going to require hand-typing a country list; fetched a real dataset instead. See `04-build-log.md` for the common-name adjustments made for a parent-facing field. *(2026-09-03: trimmed to 241 entries — dropped 8 ISO entries with no permanent civilian population — and converted from a `<select>` to a native `<input list>`/`<datalist>` typeahead with client-side list validation. `02-stack-and-schema.md` has the exact cut.)*
- Academic year format changed from a single year (`2026`) to a school year (`2026/2027`), matching how this was originally scoped back in Phase 1 — pure frontend change, no migration, since `academic_year` was always a free `CharField` with no format constraint.
- Root-caused (not guessed) a reported "Submitting… forever" bug: synchronous, per-email SMTP connection setup to Resend (~2.5-3s each, confirmed by direct measurement) was the dominant cost, not a frontend bug — fixed by reusing one connection for both emails per submission event. Separately root-caused a "Save & finish later" failure via production logs: the sidebar/save-later button weren't disabled after a successful submit, so further clicks PATCHed an already-submitted draft and got a real (correctly-behaving) 400 surfaced as a confusing generic error — fixed by locking the whole form on success, with a defensive fallback if that's ever bypassed (e.g. a stale second tab).
- **Flagged, not built** *(built 2026-09-03, see the build log's b2 entry)*: true fire-and-forget email dispatch (via Cloud Tasks) so a submission's HTTP response doesn't wait on Resend at all. Now done — `TransactionalEmail` model (migration `0015`) + a dedicated `admissions-transactional-email` queue + `TransactionalEmailSendView`, scoped to Inquiry/Application/draft-resume (Offer stays synchronous). Falls back to inline send when no queue is configured, so dev/tests are unaffected. Not yet deployed — see `docs/deployment.md`'s "Current deployment state".

## Phase 6 — Bulk/marketing email — DONE (base 2026-08-27; production incident + retry mechanism 2026-08-28; deferred "enquiries before application" scope 2026-09-01)

Full plan (schema, unsubscribe flow, sending-domain and background-job recommendations) reviewed and approved before any code was written — see `04-build-log.md` for the resolutions (plain text over HTML, pure opt-out recipient base, the `google-cloud-tasks` dependency and shared-secret internal auth, a separate bulk-sending subdomain).

**What shipped:**
- `Guardian` gained `bulk_email_unsubscribe_token`/`bulk_email_unsubscribed_at` — generated eagerly for every guardian (same pattern as `Offer.token`), checked only by the bulk-send path, never by transactional email (confirmed: `emails.py` has zero references to it).
- New `EmailCampaign` (template + optional stage/academic-year/campus filters + status) and `EmailCampaignRecipient` (the audit trail — one row per guardian per campaign, snapshotting the email address and Resend's own message id) models.
- `{{placeholder}}` substitution (guardian name(s), student name(s), a required `{{unsubscribe_link}}` — enforced at save time, not just documented) — a small whitelisted regex replace, not Django's full template engine, so a staff-authored template can't execute arbitrary template logic.
- Unsubscribe: one view handling both the RFC 8058 one-click `POST` (what Gmail/Outlook fire silently) and a human-facing `GET` confirmation page, token-only access control (same trust model as `Offer`/`ApplicationDraft`).
- Sending via Resend's HTTP batch API (up to 100 personalized emails/call), not SMTP — SMTP's one-connection-per-recipient cost is what made Phase 5's submission-latency bug possible in the first place, and would be infeasible at TCS's real family count against Resend's 10 req/sec team-wide limit.
- Cloud Tasks as the actual background-job mechanism — genuinely new GCP infrastructure (queue, IAM grants, a new pip dependency), all set up for real on the project as part of this build, not left as instructions. Queue-level rate limiting (`--max-dispatches-per-second=5`), not hand-rolled pacing in the app.
- `admissions.can_send_bulk_email` permission, not auto-granted — same deliberate-grant treatment `can_view_health_info` got, given the blast radius of sending the wrong campaign to everyone by mistake. Drafting/Preview need only normal admin access.
- Admin: Preview (renders against a real matching recipient, or placeholder text if none match yet) and Send actions, a read-only recipient-audit inline.
- **Two real bugs found and fixed while testing, not before:** a Cloud Tasks enqueue failure crashed with an unhandled 500 and left orphaned "queued" campaign/recipient rows with nothing actually dispatched — fixed by making the enqueue step atomic with a clean rollback to Draft on failure. Separately, Resend's batch API (fronted by Cloudflare) outright blocked the request with a 403 because `urllib`'s default User-Agent looks bot-like — fixed by setting an explicit one. Full detail in `04-build-log.md`.
- A dedicated bulk-sending subdomain (`updates.tcsch.edu.gh`) to isolate reputation from transactional email — DNS/Resend verification was completed by the user; **confirmed `"status": "verified"` via a real Resend `GET /domains` call on 2026-08-28** (see `04-build-log.md`). `docs/deployment.md` step 6b is the how-to for a from-scratch setup; it does not still need doing here.

**Deferred out of the Phase 6 build, deliberately (flagged during planning, not decided silently) — candidates for a scoped Phase 6.1:**
- Bounce/open tracking — needs Resend webhooks and, for opens, HTML email (this system is plain text throughout). `EmailCampaignRecipient.resend_message_id` is already stored for future correlation.
- Per-topic / multiple mailing lists — a single `bulk_email_unsubscribed_at` flag covers "all bulk email," not granular topics.

**Original Phase 6 scope:**
- ~~Enquiries before application (lead capture even without a full application)~~ — **DONE 2026-09-01**: flat `Lead` model + two public endpoints (`/api/admissions/quick-interest/`, `/api/admissions/pdf-gate/admissions-overview/`) + a Lead audience for bulk campaigns. Deployed to production 2026-09-02 (revision `admissions-00018`). See `04-build-log.md` and `02-stack-and-schema.md`.
- Lead source tracking — **partial**: `Lead.source` distinguishes the two capture points and bulk campaigns can target `filter_lead_source`. Full campaign/UTM attribution across the funnel (the `Activity` / `LeadSource` shape sketched in `01-vision.md`) is still open — deliberately, pending a decision on whether it's wanted.

## Phase 6.1 (not started, scoped) — bulk-email follow-ons
Bounce/open tracking (Resend webhooks; opens also need HTML email); per-topic / multiple mailing lists (replacing the single all-or-nothing unsubscribe flag); full lead-source / campaign attribution across the funnel. All deferred deliberately from Phase 6 — see that entry. Pick up if/when actually needed.

## Phase 7+ — Everything else in 01-vision.md
Re-enrolment, interviews/assessments as structured records, review rubrics, multi-stage review, alumni/advancement, AI analytics. Multi-campus removed from this list — Phase 5 built it. Revisit `01-vision.md` when you get here, don't pre-build models for these now.
