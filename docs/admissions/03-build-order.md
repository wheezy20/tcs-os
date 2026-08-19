# TCS Admissions Module — Build Order

**Status:** active work plan. Update this file as phases complete, scope shifts, or new phases get defined. Mark a phase `DONE` (with date) instead of deleting it, so the history stays visible.

**Current phase: Phase 4 — not started**

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

**Still not done, no phase currently owns it:** Cloudflare (subdomain proxy, Turnstile on the form, rate-limit rule on `/api/admissions/*`). Nothing has been deployed anywhere yet — everything has been tested against local `runserver`/`gunicorn` and the real Supabase project, not a live URL.

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
- `reset_admissions_data` management command built (truncates all admissions tables, resets ID sequences, requires typing "yes" to confirm) — **built but deliberately not run, and not yet committed**; exists for wiping Phase 1/2 test data immediately before go-live

**Provisional:** none of these three formats have been confirmed against any pre-existing TCS paper/legacy student-numbering convention. Confirm before treating this as final — see `02-stack-and-schema.md`.

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
- Generic file-attachment support added to `admissions/emails.py` (`_send()` now uses `EmailMessage` instead of `send_mail()` for attachment support) — a directory (`ADMISSIONS_ATTACHMENTS_DIR`) plus a per-email-type filename list (`INQUIRY_EMAIL_ATTACHMENTS`, currently empty), not hardcoded to any one document. A missing configured file is logged and skipped, never blocks the send. Wired into the Inquiry parent-confirmation email as the flagship use; extending to other email types is a one-line change once needed.

**A real deployment risk caught and avoided, not just discovered after the fact:** the plan was to point Django's `STATICFILES_DIRS` at the existing `frontend/assets/` directory to avoid duplicating the logo files. Caught before implementing: the Dockerfile's `collectstatic` runs at build time inside a `backend/`-scoped build context, which may not include the sibling `frontend/` directory depending on how the image is built — a `STATICFILES_DIRS` entry pointing outside that context would silently work in dev and break on first real deploy. Copied the 6 logo files into `admissions/static/admissions/branding/` instead (Django's standard per-app static convention, guaranteed inside the build context) — at the cost of the assets existing in two places (`frontend/assets/branding/` for the standalone HTML forms, `backend/admissions/static/admissions/branding/` for Django) that need manual sync if the brand assets are ever updated.

## Phase 5 — CRM & marketing layer
- Enquiries before application (lead capture even without a full application)
- Lead source tracking
- Newsletter/bulk communication tooling (ties into the branded email TCS already wants for the Preschool–JHS admissions announcement)

## Phase 6+ — Everything else in 01-vision.md
Re-enrolment, interviews/assessments as structured records, review rubrics, multi-stage review, alumni/advancement, AI analytics, multi-campus. Revisit `01-vision.md` when you get here, don't pre-build models for these now.
