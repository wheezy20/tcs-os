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

### Merge Phase 1 — HR module port (in progress)

- [x] Session 1: Django models (`Employee`, `EmployeePayConfig`,
      `PayrollRun`, `Payslip`, `AllowanceType`, `PAYEBand`,
      `StatutoryRate`) under `backend/hr/`
- [x] Session 2: `calculate_payslip()` pure function
- [x] Session 3: Real GRA 2024 PAYE bands + statutory rates seeded, a
      PAYE rounding-order bug found and fixed, verified against a real
      ERP payslip (Emmanuel Ansah, basic 6,500) to the cent
- [ ] Session 4: `admin.py` registration
- [ ] Session 5: Test suite (pytest/Django), built from the verified
      Emmanuel Ansah payslip as ground truth
- [ ] Session 6: Views/URLs for the payroll workflow (create run,
      generate payslips, Draft → Ready for Review → Posted)
- [ ] Session 7: Employee-generated documents (Appointment Letter,
      Probation Letter, Contract), ported from the ERP's
      `employee_generated_documents` design

### Merge Phase 2 — Finance module port

- [ ] Session 1: Models (`Expense`, `ExpenseCategory`, `Account`,
      `JournalEntry`) from the ERP schema
- [ ] Session 2: `post_payroll_run()` logic ported to Python, generating
      journal entries the same way
- [ ] Session 3: Accounting views (chart of accounts, expense entry,
      approvals)

### Merge Phase 3 — Data migration & validation

- [ ] Session 1: Management command to migrate any real ERP config/seed
      data into TCS OS (no real payroll history exists yet — the ERP has
      only run test payroll)
- [ ] Session 2: A real payroll month run in both systems in parallel,
      line-by-line payslip comparison
- [ ] Session 3: Cutover — retire the ERP's Cloudflare Worker and its
      Supabase project

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