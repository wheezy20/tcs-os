# TCS OS — Plan

The full roadmap, broken into phases. This is the file to check before
starting any new work — only build what the **active phase** below calls
for. A phase gets marked `DONE (date)` when it ships, never deleted, so
the history stays visible. New ideas that come up mid-build and aren't
part of the active phase get added under **Backlog / someday** at the
bottom, not built on the spot.

---

## Where things stand, in one paragraph

TCS OS is a single Django project, one app per module, replacing a
scattered set of standalone tools (a separate TanStack/Supabase ERP for
HR and Finance, a Google Apps Script HR system, paper processes for
everything else). Admissions is fully built and live. The ERP merge is
the active phase — folding the standalone TCS ERP's HR and Finance logic
into this same Django project, so there's one stack instead of two.
Everything after that is new modules, built one at a time as TCS
actually needs them.

---

## DONE — Admissions module

Built as a standalone module before the merge work started. Fully
self-contained documentation lives in `docs/admissions/` (its own
vision/schema/build-order/build-log five-file set) — this file doesn't
duplicate that detail, just the headline.

Covers: inquiries, applications, documents, decisions/offers/enrollment
gating, branding, deployment (`admissions.tcsch.edu.gh`), bulk/marketing
email, lead capture, and grade-band coordinator RBAC. Live in production.
See `docs/admissions/03-build-order.md` for the exact phase-by-phase
history and `docs/admissions/04-build-log.md` for the session log.

---

## ACTIVE PHASE — ERP merge

**Why:** TCS ERP (a separate TanStack Start + Supabase + Cloudflare
Workers app) and TCS OS are two stacks, two databases, two deploy
pipelines, covering different parts of the same school. Eyram is a solo
maintainer — two of everything doubles the cost of every future feature
and risks the same person (e.g. a staff member) being modeled twice and
drifting out of sync. TCS OS is the target: more mature (RBAC, email
infra, deploy discipline already in place), and Python/Django lines up
with Eyram's data science path. The ERP's business logic (payroll rules,
the approval-workflow shape, the document-generation design) is what's
valuable and portable — the plpgsql/React code itself isn't reusable
across stacks and gets rewritten, not copied. See `docs/DESIGN.md`'s
payroll section for the corrected statutory reference numbers being
ported.

### Merge Phase 0 — ERP bug fix — DONE (2026-09-24)

Standalone, blocks nothing else: found and fixed a statutory rate mixup
in the ERP's own `create_payslip()` mid-session (a proposed "correction"
based on an unverified premise was itself wrong; reverted, and the
original ERP logic confirmed correct against Ghana's real Tier 1/Tier 2
split). Full incident writeup in `docs/DESIGN.md`. No real payroll had
run on the incorrect version — ERP was still in test-run status
throughout, so no back-pay or compliance issue.

### Merge Phase 1 — HR module port — DONE (2026-09-24)

- [x] Session 1: Django models (`Employee`, `EmployeePayConfig`,
      `PayrollRun`, `Payslip`, `AllowanceType`, `PAYEBand`,
      `StatutoryRate`) under `backend/hr/`
- [x] Session 2: `calculate_payslip()` pure function
- [x] Session 3: Real GRA 2024 PAYE bands + statutory rates seeded, a
      PAYE rounding-order bug found and fixed, verified against a real
      ERP payslip (Emmanuel Ansah, basic 6,500) to the cent
- [x] Session 4: `admin.py` registration — see `docs/JOURNAL.md`'s
      2026-09-24 entry. Two items deliberately deferred rather than
      built here: `Employee` `position`/`department` fields (a schema
      change, out of scope for this session — port later using the
      ERP's existing positions/departments reference-list pattern,
      whenever more `Employee` fields get ported from the ERP
      generally) and a dedicated payroll permission (e.g.
      `hr.can_manage_payroll`) — build at Session 6, once the payroll
      workflow below gives it something real to gate, same timing
      admissions' `can_decide` followed.
- [x] Session 5: Test suite (pytest/Django), built from the verified
      Emmanuel Ansah payslip as ground truth — 15 tests, 100/100 passing.
      Deliberately deferred: allowance/overtime handling in
      `calculate_payslip()` has no coverage (ground-truth case has zero
      of either) — recorded as an open checklist item in
      `docs/CONSTRAINTS.md` rather than left silent
- [x] Session 6: Views/URLs for the payroll workflow (create run,
      generate payslips, Draft → Ready for Review → Posted, reject) —
      see `docs/JOURNAL.md`'s 2026-09-24 entry. Added the deferred
      `hr.can_process_payroll`/`hr.can_approve_payroll` permissions
      (Session 4's note above) via migrations 0003/0004, plus a
      model-level `Payslip.save()`/`delete()` immutability gate once a
      run is posted. Templates deviate from the project's default
      hand-built-HTML convention (Tailwind CDN + Alpine.js) — see
      `docs/DESIGN.md`'s new frontend-toolchain section. Deliberately
      not built: a maker-checker check stopping the same user from both
      processing and approving a run — recorded as a known boundary,
      not an open item, in `docs/CONSTRAINTS.md`'s "Payroll approval"
      section, with its own revisit trigger (a second person holding
      payroll access).
- [x] Session 7: Employee-generated documents (Appointment Letter,
      Probation Letter, Contract-Teaching, Contract-Non-Teaching) ported
      from the ERP's `employee_generated_documents` design — see
      `docs/JOURNAL.md`'s 2026-09-24 entry. One generalized
      `EmployeeGeneratedDocument` lifecycle table plus `ContractTemplate`
      (seeded blank), 6 new plain `Employee` fields, server-side
      WeasyPrint rendering, a new `hr-documents` Supabase bucket, and the
      project's first Employee detail page. Two races/bugs caught by a
      code-reviewer pass and fixed within the session (a version-number
      race on first-ever generation, and a missing `HrStorageError`
      catch on the view-link action). Deliberately not built: a 4th
      "Discarded" status (a Draft is deleted outright instead — never
      issued, nothing to preserve). Genuinely still outstanding: the
      `hr-documents` Supabase bucket itself hasn't been created in the
      dashboard yet (manual infra step, code and the
      `configure_hr_storage_bucket` command are ready) — a
      `docs/deployment.md` item, not a code gap.

### Merge Phase 2 — Finance module port

- [x] Session 1: Models (`Account`, `ExpenseCategory`, `Expense`,
      `JournalEntry`, `JournalLine`) from the ERP schema — see
      `docs/JOURNAL.md`'s 2026-09-26 entry. New `backend/modules/finance/`
      app; `ReferenceCounter` relocated out of admissions into a shared
      `tcs_os/reference_counter.py` (pure relocation, zero admissions
      regressions); real double-entry integrity enforced at both
      `clean()` and a DB `CheckConstraint`; chart-of-accounts seed
      migration (0002) loads 46 real accounts sourced by reading the
      ERP's own migration files directly off this machine (the files the
      original request named don't exist in this repo) — account 5146
      confirmed absent from the ERP entirely, not invented. Deliberately
      not built this session: `admin.py` registration (deferred to a
      later dedicated session, matching hr's precedent).
- [x] Session 2: `post_payroll_run()` logic ported to Python — see
      `docs/JOURNAL.md`'s 2026-09-26 "Finance Session 2" entry. Step 0
      integration check found `PayrollRun.branch` (hr) was still a plain
      `CharField`, never migrated to FK `admissions.Campus` despite
      Session 1 establishing that pattern for finance's own models —
      migrated it (hr migration 0008), a clean schema-only change since
      no real payroll has ever run anywhere. New
      `modules/finance/posting.py::post_payroll_run()` aggregates a
      run's payslips into one `JournalEntry`, reproducing the ERP's
      confirmed-correct statutory scheme (SSNIT employee + employer
      self-balancing pair on 2310, Tier 2 employee-only, no account
      5146 anywhere) — wired into hr's `PayrollRunApproveView` so
      approving a run now actually posts the ledger entry, closing the
      race two concurrent approvals could otherwise hit via the same
      `select_for_update()` pattern hr Session 6 used for `Payslip`
      immutability. Deliberately not built this session: no fix to
      `Payslip._payroll_run_is_posted()`'s own weaker (unlocked) read —
      recorded as a known gap in `docs/CONSTRAINTS.md`.
- [x] Session 3: Accounting views (chart of accounts, expense entry) —
      see `docs/JOURNAL.md`'s 2026-09-26 "Finance Session 3" entry. New
      `can_manage_accounts`/`can_record_expenses` permissions
      (Administration only, no separate Accountant group yet — same
      reasoning as hr Session 6); new `ExpenseCategoryAccount` model
      mapping each category to its ledger account; real ExpenseCategory
      seed data (Session 1 never actually seeded any) for both Main and
      Annex campuses. New `modules/finance/posting.py::post_expense()`
      auto-posts on expense creation, called explicitly from the view
      like `post_payroll_run()` — but idempotent by returning the same
      `JournalEntry` on a repeat call (via a new `JournalEntry.expense`
      OneToOneField) rather than rejecting, since this is an automatic
      creation-time side effect, not a repeatable user action. New
      chart-of-accounts, expense entry/list, and journal entry
      list/detail views; a new `finance-receipts` Supabase bucket
      (same signed-URL pattern as admissions); extracted the
      hr-only staff-view auth mixin into shared
      `tcs_os/staff_views.py`. A code-reviewer pass caught and fixed
      before commit: a duplicate/broken `ExpenseCategoryAccount.__str__`
      (undetected by the existing test suite), a missing layer-2/3
      upload validation on the receipt endpoint, a missing
      campus/category cross-check on expense creation, and a silent
      under-seed fallback in migration 0005 (now a loud warning).

### Merge Phase 3 — Data migration & validation

- [x] Session 1: Parity validation between the ERP and TCS OS payroll —
      see `docs/JOURNAL.md`'s 2026-09-26 "Merge Phase 3 Session 1" entry.
      Turned out to be a parity check of the real payroll workflow
      rather than a config/seed-data migration (no real payroll history
      exists in the ERP to migrate — it has only ever run test payroll),
      so this session seeded the ERP's four test employees verbatim via
      a new `seed_parity_test_employees` management command, then drove
      one real `PayrollRun` through the actual Session 6 staff-facing
      views end-to-end via a new `run_parity_test_payroll` command.
      Result: Emmanuel Ansah's payslip (SSNIT, Tier 2, PAYE, net pay,
      SSNIT employer) matches `docs/DESIGN.md`'s independently-confirmed
      ERP ground truth exactly, to the cent; the other three employees
      (Ebenezer Addo, Ama Owusu, Kojo Boadu) produced payslips but have
      no verified real ERP payslip anywhere in this project to compare
      against yet, so they're flagged "needs confirmation," not assumed
      correct. Both new commands are dev/test-database-only (approving
      the run posts a real `JournalEntry` to the finance ledger) —
      stated in their own docstrings.
- [ ] Session 2: A real payroll month run in both systems in parallel,
      line-by-line payslip comparison, extending Session 1's parity
      check to full confirmation for all four employees (currently only
      Emmanuel Ansah is confirmed)
- [ ] Session 3: Cutover — retire the ERP's Cloudflare Worker and its
      Supabase project

---

## DONE — Staff UI navigation pass (2026-09-27)

Standalone, cuts across every module rather than belonging to one merge
phase. Fixed three real navigational gaps found once HR/Finance's
staff-facing pages existed to expose them:

- Curated Django admin sidebar (`UNFOLD["SIDEBAR"]`), grouped by module
  with icons, replacing the default flat alphabetical-by-app list —
  commit `8eef01f`.
- A shared top nav bar (Payroll / Accounting / Admin, active-state
  highlighted) added to `hr/base.html` and `finance/base.html`, which
  previously had no way to move between the two staff-facing areas or
  back to admin — commit `fb27348`.
- `PayrollRunListView` — previously a `PayrollRun` was only reachable
  by already knowing its `pk`; there was no page listing all runs —
  commit `6152e32`.

See `docs/JOURNAL.md`'s 2026-09-27 entries for the full writeup,
including a dead `SITE_SUBHEADER` config setting found and documented
in place (not fixed — see that entry for why).

---

## NEXT — New modules (order not yet fixed; build whichever TCS actually
## needs first)

Each gets its own `docs/<module>/` five-file set once it starts,
mirroring `docs/admissions/`'s pattern — `00-README.md`, `01-vision.md`,
`02-stack-and-schema.md`, `03-build-order.md`, `04-build-log.md`.

- **Students** — enrollment, records, transfers, graduation
- **Academics** — curriculum, timetable, marks, grade reporting
- **Families** — guardians, contact info, relationships to students
  (note: admissions already has `Family`/`Guardian` models — decide at
  build time whether this module extends those or is genuinely separate)
- **Health / Welfare** — medical records, counseling, student support
- **Transport** — bus routes, driver/attendant records, feeds the
  separate Troute GPS tracking project
- **Marketing** — email campaigns, leads, UTM tracking; admissions
  already has a working `EmailCampaign`/`Lead` implementation with
  RFC 8058 compliance — this module likely generalizes that rather than
  building fresh

Deliberately not building yet, until TCS staff actually asks: Library,
Events, Research, Boarding, Fundraising, Catering, Alumni, and the rest
of the long department list a generic school-ERP taxonomy suggests.

---

## Backlog / someday

Ideas surfaced during other work, logged so they aren't lost, not
scheduled:

- Full funnel/campaign attribution (`Campaign`, `LeadSource`, `Activity`
  models) for admissions marketing — the flat `utm_*` fields on `Lead`
  are sufficient until TCS runs structured paid campaigns needing spend
  justification. See `docs/admissions/03-build-order.md`'s Phase 6.1.
- Bounce/open tracking and per-topic mailing lists for bulk email
  (needs Resend webhooks; opens need HTML email, currently plain text).
- Timetabling — flagged during early admissions research as a genuine
  gap in comparable school-management products; a real differentiator if
  built well, not just parity.