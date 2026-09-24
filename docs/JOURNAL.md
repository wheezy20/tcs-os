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