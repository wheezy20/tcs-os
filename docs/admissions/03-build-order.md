# TCS Admissions Module — Build Order

**Status:** active work plan. Update this file as phases complete, scope shifts, or new phases get defined. Mark a phase `DONE` (with date) instead of deleting it, so the history stays visible.

**Current phase: Phase 3 — not started**

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

## Phase 3 — Decisions, offers, enrolment

- Decision model (Accepted/Waitlisted/Rejected) with authorised-role restriction
- Waitlist + basic capacity tracking per year group
- Offer generation + parent acceptance step
- Enrolment confirmation → Student record activation

**Note:** `Student.student_id` assignment on `stage="enrolled"` already exists and is tested (Phase 2.5) — Phase 3 doesn't need to rebuild that part, only add real gating in front of it. Right now the admin's bulk "Move to Enrolled" action lets staff jump any `Application` straight to `stage="enrolled"` in one click, no Decision or Offer required — this contradicts `01-vision.md`'s "Enrolment cannot occur without required acceptance conditions met." Flagged, not yet fixed — worth resolving as part of this phase's Decision/Offer work rather than as a separate stopgap.

## Phase 4 — CRM & marketing layer
- Enquiries before application (lead capture even without a full application)
- Lead source tracking
- Newsletter/bulk communication tooling (ties into the branded email TCS already wants for the Preschool–JHS admissions announcement)

## Phase 5+ — Everything else in 01-vision.md
Re-enrolment, interviews/assessments as structured records, review rubrics, multi-stage review, alumni/advancement, AI analytics, multi-campus. Revisit `01-vision.md` when you get here, don't pre-build models for these now.
