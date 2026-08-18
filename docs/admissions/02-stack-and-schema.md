# TCS Admissions Module — Schema & API

**Status:** active reference. Update whenever a real schema or endpoint decision is made or changed. Assumes the shared infrastructure in `../shared-stack.md` (Django project, Supabase, RBAC pattern, hosting, Cloudflare), this file only covers what's specific to admissions.

## Where this lives in TCS OS

- Django app: `backend/admissions/`
- Storage bucket: `admissions-documents` (namespaced within the shared Supabase project)
- Public form subdomain: `admissions.tcsch.edu.gh`

## Phase 1 domain model

```
Family (referral_source, comments)
 ├── Parent/Guardian ×1-2 (name, email, phone, relationship, religion, address, town/city)
 └── Student ×1-5 (name, DOB, current school, current grade)
      └── Application  (one per student — academic_year, grade applied for, month of enrollment)
           ├── ApplicationStage  (Inquiry → Application → Document Review → Offer → Enrolled)
           ├── Document          (type, file_path in Supabase Storage, status: required/pending_review/approved/rejected)
           └── Notes (internal, staff-only)
```

A single inquiry submission can cover multiple children (siblings) and up to two guardians in one call — see `InquirySerializer` in `admissions/serializers.py` for the exact nested shape.

Django models (rough, refine when building):

- `Family`
- `Guardian` (FK to Family)
- `Student` (FK to Family)
- `Application` (FK to Student; stage field; academic_year; year_group applied for)
- `Document` (FK to Application; type; file_path — a Supabase Storage object path, not a URL; status)
- `Note` (FK to Application; staff-only)

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

### Email notifications

One parent confirmation + one internal staff alert per submission event (not
per child — a 2-sibling Inquiry still sends exactly 2 emails). Plain text,
`admissions/emails.py`, sent via `EMAIL_BACKEND` (console in dev). Failures are
logged and swallowed, never block the actual Inquiry/Application from saving.

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

## Admissions-specific roles (built on the shared RBAC pattern)

- Admissions Officer, Reviewer, Admin — Django Groups scoped to the `admissions` app's models only.

## Open questions to resolve before/during Phase 1

- Exact required documents per year group (Preschool through JHS may differ)
- Who are the actual admissions staff roles at TCS right now, and what should each see?
- Application fee: does TCS charge one? If yes, a payment step needs to enter the plan; if no, skip payment concerns entirely for now.
