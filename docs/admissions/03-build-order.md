# TCS Admissions Module — Build Order

**Status:** active work plan. Update this file as phases complete, scope shifts, or new phases get defined. Mark a phase `DONE` (with date) instead of deleting it, so the history stays visible.

**Current phase: Phase 1 — not started**

---

## Phase 1 — Working inquiry pipeline
Build this first, fully, before anything else.

- Django app `admissions` added to the shared TCS OS project, connected to the shared Supabase Postgres
- Models: Family, Guardian, Student, Application, Document, Note
- Django admin registered for these models (shared `django-unfold` theme already applied at the project level)
- One DRF endpoint: `POST /api/admissions/inquiries/`
- Public inquiry form (Lovable/Claude) on admissions.tcsch.edu.gh → posts to that endpoint
- File upload flow: frontend → Supabase Storage (`admissions-documents` bucket) → URL → Django
- Cloudflare: subdomain proxied, Turnstile on the form, basic rate limit rule on `/api/admissions/*`

**Definition of done:** a real parent can submit an inquiry with a document upload, and TCS staff can see and update it in the branded admin.

---

## Phase 2 — Review workflow
- Application stages become a real pipeline (kanban-style view in admin or a lightweight custom staff view)
- Document approve/reject flow with reviewer tracking
- Automated email on stage change (inquiry received, documents needed, decision made)
- Basic audit log (who changed what, when)
- Simple applications-by-stage dashboard

## Phase 3 — Decisions, offers, enrolment
- Decision model (Accepted/Waitlisted/Rejected) with authorised-role restriction
- Waitlist + basic capacity tracking per year group
- Offer generation + parent acceptance step
- Enrolment confirmation → Student record activation

## Phase 4 — CRM & marketing layer
- Enquiries before application (lead capture even without a full application)
- Lead source tracking
- Newsletter/bulk communication tooling (ties into the branded email TCS already wants for the Preschool–JHS admissions announcement)

## Phase 5+ — Everything else in 01-vision.md
Re-enrolment, interviews/assessments as structured records, review rubrics, multi-stage review, alumni/advancement, AI analytics, multi-campus. Revisit `01-vision.md` when you get here, don't pre-build models for these now.
