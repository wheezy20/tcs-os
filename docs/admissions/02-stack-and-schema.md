# TCS Admissions Module — Schema & API

**Status:** active reference. Update whenever a real schema or endpoint decision is made or changed. Assumes the shared infrastructure in `../shared-stack.md` (Django project, Supabase, RBAC pattern, hosting, Cloudflare), this file only covers what's specific to admissions.

## Where this lives in TCS OS

- Django app: `backend/admissions/`
- Storage bucket: `admissions-documents` (namespaced within the shared Supabase project)
- Public form subdomain: `admissions.tcsch.edu.gh`

## Phase 1 domain model

```
Family
 ├── Parent/Guardian (name, email, phone, relationship to student)
 └── Student
      └── Application
           ├── ApplicationStage  (Inquiry → Application → Document Review → Offer → Enrolled)
           ├── Document          (type, file_url, status: required/uploaded/approved/rejected)
           └── Notes (internal, staff-only)
```

Django models (rough, refine when building):

- `Family`
- `Guardian` (FK to Family)
- `Student` (FK to Family)
- `Application` (FK to Student; stage field; academic_year; year_group applied for)
- `Document` (FK to Application; type; file_url from Supabase Storage; status)
- `Note` (FK to Application; staff-only)

**Explicitly deferred** to later phases (see `03-build-order.md`): assessments, interviews, review rubrics, waitlist/capacity logic, offers/payments, re-enrolment, campaigns/lead scoring, workflow engine, AI analytics, alumni.

## Phase 1 API surface

```
POST   /api/admissions/inquiries/     (public — creates Family + Student + Application at "Inquiry" stage)
```

Everything else (staff review, stage changes, document approval) happens through Django admin in Phase 1, no separate staff-facing endpoint or UI needed yet.

## Admissions-specific roles (built on the shared RBAC pattern)

- Admissions Officer, Reviewer, Admin — Django Groups scoped to the `admissions` app's models only.

## Open questions to resolve before/during Phase 1

- Exact required documents per year group (Preschool through JHS may differ)
- Who are the actual admissions staff roles at TCS right now, and what should each see?
- Application fee: does TCS charge one? If yes, a payment step needs to enter the plan; if no, skip payment concerns entirely for now.
