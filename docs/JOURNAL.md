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

---

## 2026-09-26 — Finance Session 1: chart of accounts + double-entry bookkeeping models

Opened Merge Phase 2 with a new `backend/modules/finance/` app
(`modules.finance` import path, `finance` app label per `CLAUDE.md`'s
convention). Before writing any finance-specific code, relocated
`ReferenceCounter` out of `modules/admissions/models.py` into a new
shared `backend/tcs_os/reference_counter.py`, so both admissions and
finance share one atomic sequential-numbering utility — the same
"extract shared code, don't cross-import between module apps" pattern
already established for `tcs_os/text_merge.py` in HR Session 7.
`Meta.app_label = "admissions"` on the relocated class keeps it on
admissions' existing migration history/table regardless of where its
Python source now lives. Confirmed a pure relocation, not a behavior
change: `makemigrations --check` reported zero changes, and all 85
existing admissions tests passed completely unchanged.

Models: `Account` (`normal_balance` implemented as a derived
`@property`, never stored, so it can't drift from `category` — Django
has no portable equivalent to the ERP's Postgres generated column);
`ExpenseCategory` (campus-scoped, `unique_together` with campus);
`Expense` and `JournalEntry` (real text primary keys,
`"EXP-YYYY-MM-####"`/`"JE-YYYY-MM-####"`, generated once in `save()` via
the shared `ReferenceCounter`, keyed off the record's own accounting
date rather than "now"); `JournalLine` (the three double-entry
integrity rules — debit >= 0, credit >= 0, never both positive, never
both zero — enforced at both `clean()` and a DB `CheckConstraint`, with
the constraint being the real guarantee, matching the two-layer
validation pattern already documented from HR Session 6/7). `PROTECT`
used on every FK carrying real referential weight, matching this
project's "never silently orphan a financial record" convention.
`Account.created_by` was made nullable — not part of the original
spec — after finding the ERP's own migrations resolve the identical
problem the same way for its system-seeded rows; read directly from the
ERP's migration file rather than assumed.

The chart-of-accounts seed migration (0002) hit a real blocker
mid-session: the ERP migration files the original request named don't
exist in this repo, and nothing in this conversation had previously
extracted the account data. Rather than approximate from a
conversational restatement, the actual files were located and read
directly from the sibling ERP project on this machine
(`/home/wheezy20/projects/tcs-erp/supabase/migrations/`):
`20260805080000_chart_of_accounts.sql` (table structure only, no seed
rows), `20260819090000_seed_gap_accounts_expense_categories.sql` (the
real 41-row base chart), `20260909110000_post_payroll_run.sql` (adds
accounts 2310/2320/2330/4910), and
`20260909120000_payslip_employer_ssnit.sql` (adds account 5145). The
seed migration loads all 46 accounts from that verified data — the 41
base accounts plus the 5 payroll-related accounts, seeded ahead of
Session 2's ledger-posting work even though hr doesn't post to the
ledger yet. One account code from the original request, 5146, was
searched for across the entire ERP repository and does not exist
anywhere — not seeded, flagged in the migration's own docstring rather
than invented, and noted as consistent with Ghana's Tier 2 pension
scheme being 100% employee-funded (an "Employer Tier 2" expense account
wouldn't make sense as a concept regardless). Same treatment as the
Session 5 allowance/overtime test-coverage gap: a decision made with
evidence, not a stub left in place of missing data.

No `admin.py` registration this session — models and tests only. A
code-reviewer pass flagged this as reading like an oversight; recorded
here as a deliberate scope decision instead, matching hr's own
precedent (Session 4/6) of building models and tests first and
registering admin in a dedicated later session.

Verified: `manage.py test` (185/185 — 85 admissions unchanged + 65 hr +
35 finance new), `manage.py check`, `makemigrations --check --dry-run`
— all clean. Commit `67e0d2c`.

---

## 2026-09-26 — Finance Session 2: posting Payroll Runs to the general ledger

Step 0, before any posting code was written: an explicit integration
check on whether `PayrollRun.branch` (hr, Session 1) had already been
migrated to an FK against `admissions.Campus`, matching the pattern
Finance Session 1 established for its own models (`Expense`/
`JournalEntry`/`ExpenseCategory` all FK to `Campus`). It hadn't — still
a plain `CharField` left over from hr's original build. Migrated it
(migration 0008, hr) to `ForeignKey(admissions.Campus,
on_delete=PROTECT)`. A clean schema-only change with no data migration
needed, since no real payroll has ever run anywhere on this system
(confirmed against hr's own docs and the fact the ERP itself has only
ever run test payroll). The create-Payroll-Run view/template moved from
a free-text branch input to a `Campus` dropdown, and every test/query
site that referenced `branch` as a plain string was updated to match.

Built `modules/finance/posting.py`'s `post_payroll_run()` — a Python
port of the ERP's own `post_payroll_run()` RPC, specifically the
CORRECT, post-Tier-2-incident-revert scheme (see the 2026-09-24 entry
above and `docs/DESIGN.md`'s payroll section for the incident itself,
and Finance Session 1's confirmation that account 5146 doesn't exist
anywhere in the ERP): SSNIT has both an employee side (0.5%, credits
account 2310) and an employer side (13%, debits account 5145 **and**
credits that same account 2310 a second time — a self-balancing pair
sitting on top of the main entry, exactly matching the ERP's own
design); Tier 2 has only an employee side (5%, credits account 2320) —
there is no employer-side Tier 2 line or account anywhere in this
system. Every `Payslip` on a run is aggregated into **one** `JournalEntry`
(not one per payslip). Account codes are looked up once through a small
lookup dict (`ACCOUNT_CODES`), never hardcoded as scattered magic
strings through the line-building logic. Zero-amount lines are skipped
before the insert is attempted, rather than leaving `JournalLine`'s own
DB `CheckConstraint` to catch it as a failure. The entry's balance
(debit == credit) is verified by raising `PostingError` if it doesn't
hold — deliberately not a bare Python `assert`, since `assert` is
silently stripped under `python -O` and this is a real financial-
integrity guarantee, not test scaffolding. This was caught and fixed
proactively mid-session, before a code-reviewer pass even finished
reading the file — not a reviewer finding.

Wired into hr's existing `PayrollRunApproveView` (Session 6): approving
a Ready-for-Review run now actually calls `post_payroll_run()` and
creates the real `JournalEntry`, rather than Session 6's original
placeholder behaviour of just flipping status with no ledger effect
(that session's own view docstring had explicitly flagged this as
future, out-of-scope work — this session is that work).
`post_payroll_run()` re-reads the `PayrollRun` row with
`select_for_update()` inside its own `transaction.atomic()` block and
re-checks status there before creating anything, closing the same
cross-request race (two concurrent approval attempts, each holding its
own independently-fetched `PayrollRun` object) that hr Session 6's
`Payslip._payroll_run_is_posted()` fix closed for a different code
path. A code-reviewer pass separately noted — not a regression from
this session, an observation for a future one — that
`Payslip._payroll_run_is_posted()` itself actually uses a *weaker*,
unlocked read (no `select_for_update()`) than this session's own
pattern, so the two "fresh DB read" checks in the codebase aren't
actually equivalent in strength. Flagged in `docs/CONSTRAINTS.md` as a
known, deferred gap rather than left only here.

Tests: a realistic two-employee posting scenario in
`modules/finance/tests.py` — Emmanuel Ansah's real, independently-
verified ERP ground-truth payslip (generated via the actual
`calculate_payslip()`, not hardcoded numbers) plus a second, hand-
constructed payslip with non-zero fines/iou specifically to exercise
those two optional journal lines. Every expected aggregate and line
figure was independently hand-added from each payslip's own stored
fields, not derived by calling `post_payroll_run()` and trusting its
own output. Confirms: the entry balances (debit = credit = 10,735.00
for this scenario), every line's account and amount is correct, the
SSNIT self-balancing pair produces two genuinely distinct
`JournalLine` rows on account 2310 (not merged into one), zero-value
lines are correctly absent, a Draft-status or payslip-less run can't be
posted, and a second post attempt is rejected cleanly without creating
a duplicate entry. hr's own `PayrollRunLifecycleTests` (`hr/tests.py`)
was extended to confirm the real `JournalEntry` appears after using the
actual HTTP approve view — an integration check on top of finance's own
unit tests — plus a dedicated test confirming a second approve-view
POST doesn't create a duplicate.

A code-reviewer pass found no blocking issues. Confirmed correct: the
SSNIT self-balancing pair, the idempotency/race-closing pattern, that
migration 0008 is safe against Postgres specifically because hr has
never been deployed anywhere real yet (confirmed via
`docs/deployment.md`), the `Campus`-as-branch string interpolation in
error messages, the one-directional cross-app import (`finance.posting`
imports `hr.models`; `hr.views` imports `finance.posting`; no cycle),
and that the statutory scheme in the actual code matches the
confirmed-correct one (no 5146, no employer Tier 2 line).

Verified: `manage.py test` (192/192 — 85 admissions unchanged + 66 hr +
41 finance), `manage.py check`, `makemigrations --check --dry-run` —
all clean. Commit `a19da39`.

---

## 2026-09-26 — Finance Session 3: accounting views + expense auto-posting

Two real gaps found between the task spec and actual repo state this
session, both resolved with stated reasoning rather than silently: (1)
no source-tracing mechanism existed on `JournalEntry` at all from
Session 2 — idempotency there works purely via `PayrollRun.status`
gating, which has no equivalent for `Expense`. Added a nullable
`OneToOneField` `expense` on `JournalEntry` specifically for `Expense`
idempotency, per the spec's own explicit fallback instruction. (2)
Session 1 never actually seeded any `ExpenseCategory` rows — only
`Account` got a seed migration. This session found the real ERP data
(the same source file already read for Session 1's chart of accounts)
and seeded the real 9 category names (Transport, Fuel, Rent, Utilities,
Casual labour, Repairs & maintenance, Supplies, Staff advances,
Miscellaneous) plus their real account mapping, for **both** of TCS
OS's existing `Campus` rows (Main, Annex) — an explicit extrapolation
from the ERP's own single-branch-at-the-time data, since TCS OS already
supports two campuses unlike the ERP when that data was written.
Flagged in the migration's own comment, not silently assumed.

Built new `can_manage_accounts` (on `Account`) and `can_record_expenses`
(on `Expense`) permissions, granted to the existing Administration group
only — no separate Accountant group this session, explicitly the same
reasoning as hr Session 6's self-approval-boundary decision
(`docs/CONSTRAINTS.md`): don't build role separation nobody can use yet.
New `ExpenseCategoryAccount` model (`OneToOneField` to
`ExpenseCategory`) maps each category to its ledger account, mirroring
the ERP's own `expense_category_accounts` table.

`modules/finance/posting.py` gained `post_expense()` alongside the
existing `post_payroll_run()` — auto-posts a simple two-line entry
(debit the expense category's mapped account, credit the payment
method's asset account: 1000 Cash on Hand / 1010 Cash in Bank / 1020
Mobile Money Float) called **explicitly** from the expense-creation
view, deliberately not via a Django signal or an `Expense.save()`
override — there is no signal/save-override precedent anywhere in this
codebase for a creation-time cross-app side effect; the one real
precedent (`post_payroll_run()`, Session 2) is itself called explicitly
from a view, not from `PayrollRun.save()`, so `post_expense()` follows
that same established shape. Idempotency here is deliberately **not**
"reject a second attempt" like `post_payroll_run()`'s — a second call to
`post_expense()` for the same `Expense` returns the same `JournalEntry`
rather than raising, since this is meant to fire once as an automatic
side effect of `Expense` creation (not a repeatable, deliberate user
action the way approving a payroll run is) — backed by
`JournalEntry.expense`'s real DB-level uniqueness
(`OneToOneField`), not just an application-level pre-check, confirmed
via a test that mocks only the pre-check so a genuine race is
exercised.

New views (`modules/finance/views.py`): chart of accounts (list,
filterable by category; create/edit gated on `can_manage_accounts`;
accounts are deactivated via `is_active`, never deleted — no delete
route exists at all), expense entry (a form gated on
`can_record_expenses`, with an optional receipt upload to a new private
"finance-receipts" Supabase bucket using the exact same two-step
signed-URL handshake as admissions' document uploads; on success, shows
the resulting journal entry's id on the confirmation page so the user
can see the posting actually happened), an expense list (filterable by
campus/category/date range, each row linking to its journal entry), and
a read-only journal entries list/detail that shows entries from both
`post_payroll_run()` and `post_expense()` together.

Also extracted the staff-facing-view auth mixin (previously hr-only,
called `HrStaffRequiredMixin`) into a new shared
`backend/tcs_os/staff_views.py` (`StaffRequiredMixin`), following the
exact same "extract shared code, don't duplicate across module apps"
pattern already used for `tcs_os/text_merge.py` (hr Session 7) and
`tcs_os/reference_counter.py` (finance Session 1) — `hr/views.py`'s
`HrStaffRequiredMixin` is now a thin subclass of the shared one, not a
second full implementation, and all 66 hr tests (including the ones
that specifically exercise the 403-vs-redirect-loop distinction this
mixin provides) still pass unchanged, confirming it's a faithful
extraction.

A code-reviewer pass found two real bugs and two worth-fixing items,
**all fixed before this commit** (not left as follow-up work):

1. **(bug)** A duplicate `__str__` method on the new
   `ExpenseCategoryAccount` model — a copy-paste leftover of
   `ExpenseCategory`'s own `__str__`, referencing `self.name`/
   `self.campus` which don't exist on `ExpenseCategoryAccount`, silently
   overriding the correct definition (Python allows redefining a method
   in the same class body) and would raise `AttributeError` the instant
   anything called `str()` on this model (Django admin, a template, a
   log line) — nothing in the original test suite happened to call
   `str()` on it, so this went uncaught until manual review. Deleted the
   bad second definition, added a regression test. Worth remembering as
   its own cautionary example: a bug that a full green test suite
   (216/216 at the time) did not catch, because nothing in it happened
   to exercise `str()` on that one model.
2. **(bug)** The receipt-upload endpoint
   (`ExpenseReceiptUploadURLView`) only checked for a non-empty
   filename — missing layers 2 and 3 of this project's documented
   three-layer file-upload-validation convention (`docs/CONSTRAINTS.md`:
   client, serializer/view, and the storage bucket itself). Added
   extension and client-declared-`file_size` validation to the view
   (mirroring admissions' `UploadURLRequestSerializer` exactly) and a
   new `configure_bucket_limits()` function in `finance/storage.py` plus
   a new `configure_finance_storage_bucket` management command
   (mirroring admissions' and hr's own equivalents) for the
   bucket-level lock-down — the real, unbypassable enforcement layer.
   Added tests for both new validation checks.
3. **(worth-fixing)** `ExpenseCreateView` never checked that the
   submitted campus and category actually agreed with each other
   (they're independent form fields; nothing stopped submitting
   campus=Main with a category that only exists for Annex) — added an
   explicit mismatch check with a clear form error, plus a regression
   test.
4. **(worth-fixing)** Migration 0005's fallback for a missing mapped
   account (should never happen, given migration 0002's own dependency
   ordering, but if it ever did) silently skipped that category with
   only a code comment explaining why — changed to print a loud warning
   during migrate, consistent with this project's "never silently
   no-op" discipline already stated in `posting.py`'s own `PostingError`
   docstring.

Checked whether the three-layer file-upload-validation rule (item 2
above) was already written down anywhere as its own stated constraint,
or only ever demonstrated in code — it's already present in
`docs/CONSTRAINTS.md`'s "Lessons that cost something once" section
(added in the original doc-restructuring commit `bf2a05c`, before this
session), so no new constraint entry was needed; this session is simply
the rule's second real demonstration (admissions was the first).

Verified: `manage.py test` (216/216 — 85 admissions + 66 hr + 65
finance), `manage.py check`, `makemigrations --check --dry-run` (clean).
A real end-to-end manual smoke test was also run against every new view
via Django's test `Client` before the formal test suite was written
(account create/edit/deactivate, expense create → auto-post →
confirmation page showing the real journal entry, expense list, journal
entry list/detail). Commit `4b4337b`.

## 2026-09-26 — Merge Phase 3 Session 1: payroll parity validation (2 of 4 employees confirmed)

Read the real ERP dummy seed data directly from
`~/projects/tcs-erp/supabase/seed.sql` (the `insert into public.employees`
and `insert into public.employee_pay_config` blocks) — four employees:
Emmanuel Ansah (Head Teacher, basic 6,500, pays all three statutory
deductions), Ebenezer Addo (Accountant, basic 4,200, pays all three),
Ama Owusu (Class Teacher, basic 2,800, pays all three), Kojo Boadu
(Teaching Assistant, basic 1,500, pays none — `pays_ssnit`/`tier2`/`paye`
all false).

Added two new management commands, not migrations, to
`backend/modules/hr/management/commands/`:

- `seed_parity_test_employees.py` — idempotent (`get_or_create`-keyed),
  creates `Employee` + `EmployeePayConfig` rows for the four employees
  above, copied verbatim from the ERP data. Three fields have no ERP
  source and are flagged in the command's own docstring as reasoned
  inferences rather than presented as verbatim: `payment_method="Bank
  Transfer"` (ERP has bank/account number but no `payment_method`
  column), `employment_type="Full-time"`, and `start_date` (ERP has no
  hire-date column at all). None of the three feed `calculate_payslip()`,
  so none affect the parity comparison itself.
- `run_parity_test_payroll.py` — drives one real `PayrollRun` (Main
  campus, September 2026 — the same month already used for Emmanuel
  Ansah's confirmed ERP ground truth in `docs/DESIGN.md`) through the
  actual Session 6 staff-facing views via `django.test.Client` with
  `force_login` (create → generate payslips → submit for review →
  approve & post) — deliberately the real user-facing workflow, not a
  script calling `calculate_payslip()`/`PayrollRun.objects.create()`
  directly, since Session 5's unit tests already cover the calculation
  in isolation. It temporarily adds a scripted `parity_test_runner` user
  to the real "Administration" group (already carrying both
  `hr.can_process_payroll` and `hr.can_approve_payroll` per migration
  0004) only for the run's duration, removing that membership again in a
  `finally` block even on error — because approving the run calls the
  real `finance.posting.post_payroll_run()`, which creates a real,
  immutable `JournalEntry` in the actual finance ledger. Proving that
  real side effect is the point of a parity check, but it means this
  command must only ever run against a dev/test database, never one
  holding real financial data — stated explicitly in the command's own
  docstring, found and required by a code-reviewer pass before this was
  considered done.

Ran the full flow for real: `seed_parity_test_employees` then
`run_parity_test_payroll`. Comparison table:

- **Emmanuel Ansah** — SSNIT 32.50, Tier 2 325.00, PAYE 1,134.13, net pay
  5,008.37, SSNIT employer 845.00 — **parity confirmed**, matching
  `docs/DESIGN.md`'s independently-confirmed ERP ground truth exactly, to
  the cent.
- **Ebenezer Addo** — SSNIT 21.00, Tier 2 210.00, PAYE 590.75, net pay
  3,378.25, SSNIT employer 546.00 — **needs confirmation**: no verified
  real ERP payslip exists anywhere in this project to compare against.
- **Ama Owusu** — SSNIT 14.00, Tier 2 140.00, PAYE 353.80, net pay
  2,292.20, SSNIT employer 364.00 — **needs confirmation**, same reason.
- **Kojo Boadu** — all statutory deductions 0.00, net pay 1,500.00
  (pays no SSNIT/Tier 2/PAYE) — **needs confirmation**, same reason.

A code-reviewer pass on both new command files caught two real issues
before this was committed, both fixed: (1) the finance-ledger-posting
side effect and the temporary Administration group grant were
undocumented — fixed by adding the explicit dev/test-only docstring
warning above and the `try`/`finally` that unconditionally removes the
group membership after the run; (2) the seed command's docstring
undercounted invented fields (only flagged `payment_method`, not
`employment_type` or `start_date`, which are equally invented — the ERP
schema has neither column) — fixed by expanding the docstring to flag
all three. Also tightened the generate-payslips step to check for HTTP
200 before proceeding (previously proceeded regardless), and added a
catch-all branch for any `PayrollRun.status` the script doesn't
recognize instead of silently printing an empty comparison table.

Verified: `manage.py check` clean, `makemigrations --check --dry-run`
reports no changes (management commands only — no schema touched this
session). Full `manage.py test` run: 216 tests, 3 initially failed with
`psycopg2.OperationalError` ("SSL connection has been closed
unexpectedly" / "connection already closed") against the project's
remote Supabase-hosted dev database — all three in
`modules.hr.tests.GenerateDocumentTests`, the slowest tests due to real
WeasyPrint PDF rendering. Re-running that class in isolation gave 7/7
passing in under 30 seconds, confirming the 3 failures were a transient
connection drop against the remote pooler during the ~25-minute full
run, not a real regression.

Per `docs/PLAN.md`'s Merge Phase 3 Session 1 scope, explicitly did not
touch PLAN.md's cutover language — that's Session 3's job, gated on a
full-parity comparison table, which this session does not yet have
(only 1 of 4 employees is confirmed; the other 3 need a real ERP
payslip pulled before they count).