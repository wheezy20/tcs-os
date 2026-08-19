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
per-email-type filename list (`INQUIRY_EMAIL_ATTACHMENTS`, currently empty)
rather than hardcoding any one document. Wired into the Inquiry
parent-confirmation email as the flagship use; a missing configured file is
logged and skipped, never blocks the send (same "never let email plumbing
block the actual submission" rule as the rest of this module). Extending
attachments to Application/Offer emails later is a one-line change — pass
another settings list into that email's `_send()` call.

## Admissions-specific roles (built on the shared RBAC pattern)

- Admissions Officer, Reviewer, Admin — Django Groups scoped to the `admissions` app's models only.

## Open questions to resolve before/during Phase 1

- Exact required documents per year group (Preschool through JHS may differ)
- Who are the actual admissions staff roles at TCS right now, and what should each see?
- Application fee: does TCS charge one? If yes, a payment step needs to enter the plan; if no, skip payment concerns entirely for now.
