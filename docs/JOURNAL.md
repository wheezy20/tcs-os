# TCS OS — Journal

A running log, newest at the bottom, across **every module** — not a
full commit-by-commit record, just the decisions and milestones worth
remembering later. Append a dated entry at the end of every session,
before finishing, per `CLAUDE.md`'s standing instruction.

**Scope note:** the admissions module was built and largely completed
before this system-wide journal existed. Its own detailed, session-by-
session history (2026-08-17 through 2026-09-09, ~85 tests, full Phase
1-6.2 build) lives entirely in `docs/admissions/04-build-log.md` and
isn't duplicated here. This file's real entries begin with the ERP merge
work below; a future session touching admissions again should log here
*and* keep appending to that module's own build log, same as any other
module going forward.

---

## 2026-09-23/24 — ERP merge kickoff: HR module port begins, Tier 2 incident, doc restructuring

Decided to merge the standalone TCS ERP (TanStack Start + Supabase +
Cloudflare Workers) into TCS OS, rather than maintaining two stacks
indefinitely. Full reasoning in `docs/PLAN.md`'s "ACTIVE PHASE — ERP
merge" section. HR is the first module being ported (payroll, employees,
statutory deductions); Finance follows.

**HR module, Sessions 1-2**: Django models (`Employee`,
`EmployeePayConfig`, `PayrollRun`, `Payslip`, `AllowanceType`,
`PAYEBand`, `StatutoryRate`) under `backend/hr/`, and a pure
`calculate_payslip()` function mirroring the ERP's `create_payslip()`
logic. Verified by hand against a placeholder PAYE table before real
GRA bands existed in TCS OS.

**A real, corrected mistake — the Tier 2 incident.** Mid-session, acting
on an unverified conversational restatement of Ghana's SSNIT/Tier 2
split, both the ERP's `create_payslip()` and TCS OS's
`calculate_payslip()` were changed to a wrong scheme (SSNIT
employer-only, Tier 2 split 5%/0.5% employer/employee). Built and
briefly migrated in the ERP (caught and deleted before commit — the
migration was never pushed). The ERP's *original* logic was already
correct; both systems were reverted to it. Confirmed against the real
Ghana statutory structure (Act 766): employee 5.5% total (0.5% SSNIT +
5% Tier 2), employer 13% (SSNIT only, no Tier 2 contribution). No real
payroll had run on the incorrect version in either system — the ERP has
only ever run test payroll — so no back-pay or compliance issue. Full
writeup, including why this is worth remembering, in `docs/DESIGN.md`'s
payroll section and `docs/CONSTRAINTS.md`'s statutory-accuracy section.

**HR module, Session 3**: real GRA 2024 PAYE bands and the corrected
statutory rates seeded via an idempotent data migration
(`hr/migrations/0002_seed_statutory_data.py`). This surfaced a second,
genuine bug: `calculate_payslip()` summed full-precision PAYE band
amounts and rounded once at the end, while the ERP rounds each band's
own contribution before accumulating — the two disagree by a cent on
some inputs (confirmed: basic 6,500 gives 1,134.12 vs. the ERP's correct
1,134.13). Fixed to match the ERP's per-band rounding exactly, then
verified against a real ERP payslip (Emmanuel Ansah, Head Teacher, basic
6,500, Sept 2026) to the cent on every field: SSNIT 32.50, Tier 2 325.00,
PAYE 1,134.13, net pay 5,008.37. This is now the project's ground-truth
reference case for any future payroll-logic change.

**Documentation restructuring.** TCS OS previously had no system-wide
doc set — only `docs/shared-stack.md`, `docs/deployment.md`, and
admissions' own five-file module doc set. Read through the standalone
ERP's full doc set (`CLAUDE.md`, `DESIGN.md`, `JOURNAL.md`,
`PLANNING.md`, `STACK.md`, `CONSTRAINTS.md`) as a model, and TCS OS's own
existing admissions docs, to design a parallel system-wide set for
TCS OS: `CLAUDE.md` (repo root, auto-loaded by Claude Code),
`docs/PLAN.md`, `docs/DESIGN.md`, `docs/JOURNAL.md` (this file),
`docs/CONSTRAINTS.md` — `docs/shared-stack.md` and `docs/deployment.md`
were kept as-is (already filling the "STACK.md" and deployment-runbook
roles respectively), and `docs/admissions/` was left completely
untouched, since it's already a complete, self-contained module doc set.

Also read the full **TCS Brand Guidelines v1.0** PDF (30 May 2026) for
the first time in this project (previously only admissions' own
`brand-tokens.md` quick-reference summary had been consulted) — confirmed
the existing summary's colors/typography/logo rules are accurate, and
added the fuller tone-of-voice, personality-trait, and tagline material
to `docs/DESIGN.md`'s system-wide Branding section. Flagged two
apparent errors in the source document itself (a stray "deep pink"
reference on the Usage Proportion page that matches nothing else in the
guide, and Aqua Veil/Deep Teal Shadow sharing identical color values
under two different names) — recorded in `docs/CONSTRAINTS.md` as items
to confirm with the designer, not resolved silently.

### Still outstanding

- HR module Sessions 4-7 (`admin.py`, test suite, payroll workflow
  views, employee-generated documents) — see `docs/PLAN.md`.
- Finance module port (not started).
- The ERP-side Tier 2 fix never happened (nothing was actually wrong to
  fix, once reverted) — confirm the ERP's local `db reset` state is
  clean and the deleted migration file is genuinely gone before the next
  session touches ERP payroll again.

---

## 2026-09-24 — HR Session 4: admin.py registration

Added `backend/modules/hr/admin.py`, registering all 7 hr models
(`Employee`, `EmployeePayConfig`, `PayrollRun`, `Payslip`,
`AllowanceType`, `PAYEBand`, `StatutoryRate`) with unfold's `ModelAdmin`
via `@admin.register`, following `modules/admissions/admin.py`'s existing
conventions rather than inventing new ones. `PayslipAdmin` disables
add/change/delete outright — the same "computed, never hand-edited"
treatment as admissions' `TransactionalEmailAdmin`, and matching the
ERP's own posted/closed-record convention (a correction is a new
offsetting entry, never an edit to history). `PAYEBandAdmin` and
`StatutoryRateAdmin` carry a help-text note on `effective_from` pointing
back at `docs/CONSTRAINTS.md`'s statutory-accuracy checklist, so editing
either table in the admin doesn't feel like a free action. Verified two
ways: `manage.py check` clean, and a real Django test-`Client` hit
against `/admin/` on a throwaway sqlite db, confirming the admin index
actually renders all 7 models grouped under an "Hr" section (checked via
the literal string "Models in the Hr application" appearing in the
response). Commit `c610a19`.

Three things came up and were deliberately deferred rather than folded
into this commit — flagged for **Session 6 or 7** so a future session
doesn't have to reconstruct the reasoning:

1. **`Employee` has no `position`/`department` fields** (only
   `employee_number`, `first_name`, `surname`, `basic_salary`,
   `employment_status`, `created_at`) — the original ask for this admin
   wanted them in `list_display`/`list_filter`, but adding them is a
   schema change (a new migration) out of scope for "register the
   models," and this project's one-feature-at-a-time convention says
   model registration and model expansion shouldn't ride the same
   commit. Flagged in `EmployeeAdmin`'s docstring in `admin.py`. When
   this is ported, it should reuse the ERP's existing
   positions/departments reference-list pattern rather than inventing a
   new shape — the user was explicit that this is "a real,
   already-refined pattern worth reusing," not a from-scratch design
   question.
2. **No dedicated payroll permission yet** (something like
   `hr.can_manage_payroll`) — hr admin currently relies on plain Django
   admin permissions (`is_staff` + default model perms), nothing like
   admissions' `can_decide`/`can_view_health_info`. Flagged in a comment
   at the top of `admin.py`. Deliberately not built now: admissions'
   `can_decide` wasn't added at admissions' own initial
   model-registration time either — it came in Phase 3, once real
   approve/reject actions existed for it to gate. Payroll's equivalent
   moment is Session 6, when the Draft → Ready for Review → Posted
   payroll workflow actually gets built; a permission with nothing yet
   to protect is premature.
3. **`Payslip` is fully locked in admin (no add/change/delete)** — this
   one is a settled, confirmed-correct decision, not an open gap; see
   above and `docs/DESIGN.md`'s payroll section for the rationale. It
   does not need revisiting the way 1 and 2 do.

---

## 2026-09-24 — HR Session 5: payroll test suite

Added `backend/modules/hr/tests.py`, 15 tests covering
`calculate_payslip()`: the Emmanuel Ansah ground-truth case (basic
6,500) to the cent, the `pays_ssnit`/`pays_tier2`/`pays_paye` flags
independently, PAYE band-crossing including a regression test for the
Session 3 per-band-rounding bug, `EmployeePayConfig` effective-dating,
`PayrollConfigError` on every missing-config scenario, and a sanity
check that migration 0002 seeded real (not placeholder) GRA PAYE bands
and Act 766 statutory rates. Full suite 100/100, `manage.py check` and
`makemigrations --check --dry-run` both clean. Commit `b1c7723`.

A code-reviewer pass flagged one real, deliberate gap: `calculate_payslip()`
also handles `total_allowances`/overtime pay and `AllowanceType.taxable`
gating of the PAYE base, but none of these 15 tests exercise it — the
ground-truth case (and every other test here) has zero allowances and
zero overtime. Rather than leave that true-but-unwritten, the gap is
recorded as its own open item in `docs/CONSTRAINTS.md`'s HR/Finance
go-live checklist, separate from (and below) the now-checked "proper
test suite" item — the same lesson the Tier 2 incident already taught:
an unwritten assumption is how that happened the first time.
   does not need revisiting the way 1 and 2 do.

---

## 2026-09-24 — HR Session 6: payroll workflow views

Added the payroll workflow's views/URLs and the deferred payroll
permissions flagged back at Session 4. Migration 0003 adds
`PayrollRun.Meta.permissions` (`can_process_payroll`,
`can_approve_payroll`) plus a `rejection_reason` TextField. Migration
0004 is an idempotent data migration creating a new "Payroll Processor"
group (holds only `can_process_payroll`, nobody auto-assigned) and
granting both new permissions to the existing admissions
"Administration" group — using `.add()`/`.remove()` rather than 0017's
`.set()` pattern, specifically because Administration's ~49 existing
admissions permissions are owned by 0017, not this migration, and
`.set()` here would have wiped them. Verified idempotent in both
directions (permission count going 51↔49↔51) against a throwaway db.

`backend/modules/hr/views.py` is the first staff-facing HTML views in
the whole project — plain Django class-based views with
`LoginRequiredMixin` + `PermissionRequiredMixin`, not DRF, since this is
server-rendered staff tooling, not a JSON API. Covers the full
`PayrollRun` lifecycle: create (rejects a duplicate branch/month/year
with a clear error), generate payslips (a per-employee loop around the
already-tested `calculate_payslip()`, skipping `PayrollConfigError`
per-employee rather than failing the whole batch), submit for review,
approve & post, and reject with a required reason back to Draft.
"Posted" means locked only for now — no Finance/journal-entry posting
yet, that's Merge Phase 2.

Added a model-level immutability gate: `Payslip.save()`/`delete()` now
raise `PayrollRunLockedError` once the parent run is posted. This was
fixed twice in the same session. The first fix was view-level (a
try/except around the mutation) and only caught the case where the same
in-memory `PayrollRun` object was mutated after being fetched once. A
code-reviewer pass flagged that this would silently miss a real
concurrent-request race — two separate processes, each holding its own
independently-fetched `PayrollRun` object, where the first process's
in-memory status never sees the second process's post. The follow-up
fix changed the gate to re-query `PayrollRun.status` fresh from the
database on every `save()`/`delete()` call instead of trusting the
cached FK object, closing the actual race rather than the apparent one.
Verified via a test that mocks a genuinely separate run fetch going
stale mid-batch. Caught and fixed within the session, not left as a
known gap.

Templates (`templates/hr/`) use Tailwind (CDN `<script>`, no build
step) + Alpine.js — a deliberate deviation from the project's default
hand-built-HTML convention, recorded in `docs/DESIGN.md`'s new
"Frontend toolchain — hr's staff-facing payroll views" section.

Verified: `manage.py test modules.hr` (35/35), full suite (120/120),
`manage.py check`, `makemigrations --check --dry-run` — all clean.
Commit `2c18ea9`.

One thing considered and deliberately not built: a maker-checker
control stopping the same user from both processing and approving a
run. TCS currently has exactly one person with any of this access, so
the rule would have nothing to separate yet, and would create a dead
end the day the sole administrator needs to both process and approve
because there's nobody else. This is not the same category as the
allowance/overtime gap logged in Session 5 — that was an oversight to
eventually close; this is a reasoned, scoped boundary with its own
revisit trigger. Recorded as such in `docs/CONSTRAINTS.md`'s new
"Payroll approval — known boundary, not a gap" section, not as an open
checklist item: revisit once a second person holds
`hr.can_process_payroll` or `hr.can_approve_payroll`.

---

## 2026-09-24 — HR Session 7: employee-generated documents

Ported the ERP's `employee_generated_documents` design: one generalized
`EmployeeGeneratedDocument` lifecycle table with a `document_kind`
column covering Appointment Letter, Probation Letter,
Contract-Teaching, and Contract-Non-Teaching, rendered server-side via
WeasyPrint instead of the ERP's client-side html2canvas/jsPDF. New
`ContractTemplate` model holds an editable `html_body` per kind, seeded
blank by an idempotent migration (0006) — no letter/contract prose
invented. `Employee` gains 6 new plain fields (`position`,
`department`, `employment_type`, `payment_method`, `start_date`,
`probation_end_date`) — no reference table yet, matching Session 4's
original deferral note. New private Supabase bucket (`hr-documents`,
`modules/hr/storage.py`) mirrors admissions' signed-URL-only storage
pattern; new `configure_hr_storage_bucket` management command mirrors
admissions' equivalent. New `modules/hr/views.py` additions: the
project's first Employee detail page plus generate/issue/discard/accept/
view-link actions, gated on `hr.can_process_payroll` (Session 6's
permission, reused rather than minted new).

`EmployeeGeneratedDocument` carries two DB-level constraints: a partial
unique index enforcing at most one `Issued` row per
(employee, document_kind) — `issue_document()` must demote any existing
`Issued` row to `Superseded` first, then promote, for this index to
accept the promotion — and a unique (employee, document_kind, version)
constraint (migration 0007) added as a backstop after a code-reviewer
pass caught a narrow concurrent-request race in the version-number
computation: `select_for_update()` can't lock a row that doesn't exist
yet, so a first-ever generation for a given employee+kind had no real
protection against two simultaneous callers computing the same "next
version." The constraint turns that race into a clean
`DocumentGenerationError` instead of a raw `IntegrityError`/500. A
second code-reviewer finding, also fixed before commit:
`EmployeeGeneratedDocumentViewLinkView` was the one new view that didn't
catch its own `HrStorageError` and would have 500'd on a real signing
failure — brought in line with every other new view's clean-error
handling.

New shared `tcs_os/text_merge.py`: `render_template()` extracted out of
`modules/admissions/bulk_email.py` (re-exported there for existing call
sites) so both apps use one whitelist `{{field}}` substitution
implementation — never Django's real template engine — without a
cross-module import between the two apps.

Three decisions Eyram confirmed upfront before this session's code was
written, each a real choice rather than an obvious default: (1) extract
`render_template()` into the new shared `tcs_os/text_merge.py` rather
than a cross-module import or a duplicated copy; (2) build a real,
minimal Employee detail page (name, number, status, position,
department — no edit form) rather than just a narrower document-list
view, since no Employee-centric page existed anywhere in the project
before this session; (3) `discard_document()` deletes a Draft row
outright rather than adding a 4th "Discarded" status — a Draft was
never issued, so there's nothing to preserve a history of, unlike this
project's usual "archive, don't delete" convention.

The Dockerfile's WeasyPrint system-library list in the original task
spec was outdated, based on an older Cairo/GTK-based WeasyPrint version
— verified against WeasyPrint's own current install docs via a real web
fetch, and the correct current Debian package list
(`libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0`) used in
the Dockerfile instead.

All 4 document kinds were generated with real template content and
each PDF individually opened and visually inspected — this project's
existing "don't trust magic bytes" lesson (`docs/CONSTRAINTS.md`) — and
it caught one real bug this way: `HR_DOCUMENT_LETTERHEAD`'s
`company_address` has a real embedded newline that was collapsing to a
single line in the rendered HTML/PDF output until `build_merge_data()`
was changed to convert it to `<br>` at substitution time; the settings
constant itself was never altered.

Verified: `manage.py test modules.hr` (65/65), full suite (150/150 — 85
admissions + 65 hr), `manage.py check`, `makemigrations --check
--dry-run` (clean), and a real `docker build` — confirmed by actually
running WeasyPrint inside the built container image, not just checking
`pip install` succeeded. Commit `3856b4f`.

One thing not built this session, still outstanding: the `hr-documents`
Supabase bucket itself still needs creating in the Supabase dashboard (a
manual infrastructure step — Eyram runs all commands with real
infrastructure side effects, per `CLAUDE.md`) before this feature can be
used against real storage. The code and the
`configure_hr_storage_bucket` management command are ready; nothing in
this session created the bucket. This is a deployment-runbook item
(mirrors admissions' `docs/deployment.md` step 5b bucket-configuration
pattern) rather than a `docs/CONSTRAINTS.md` hard rule, so it isn't
logged there — worth adding to `docs/deployment.md` once hr gets its own
deploy step.