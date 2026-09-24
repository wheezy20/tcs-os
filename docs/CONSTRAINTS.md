# TCS OS — Constraints

Things that shape how this gets built, not just what gets built. System-
wide; a module can add its own constraints to its own doc set, but
anything here applies everywhere.

## Architecture — hard rules

- **One Django project, one app per module, one shared User model.**
  Never a second Django project or a separate backend for a new module.
  See `docs/shared-stack.md`.
- **Django owns all database access.** Supabase's Data API, automatic
  table exposure, and automatic RLS must stay unchecked/unused for this
  project's Supabase instance. Enabling any of them risks a second,
  uncoordinated access-control system silently sitting alongside
  Django's own permission checks. See `docs/shared-stack.md`'s Supabase
  project setup section.
- **A permission with real blast radius (health data, financial
  approval, bulk send, delete) is never auto-granted.** It gets its own
  `Meta.permissions` entry, bundled into a named Group by an idempotent
  data migration, and populating that Group with real users is always a
  deliberate go-live decision for a human — never assumed or defaulted.

## Statutory accuracy (payroll, HR module)

- **The confirmed-correct Ghana statutory scheme, do not deviate without
  re-verifying against an authoritative source or the working ERP
  reference implementation**: SSNIT Tier 1 = 13% employer-only (0.5%
  employee, 13% employer); Tier 2 = 5% employee, 0% employer. See
  `docs/DESIGN.md`'s payroll section for the full incident writeup on
  why this line exists.
- **PAYE bands are sourced from GRA's published "Year 2024" schedule**,
  monthly thresholds, with the top-band threshold set to the literal
  50,000.00 GRA states rather than the arithmetic-sum figure of
  50,416.67 the source table's own width column implies. **Not
  re-confirmed against any GRA revision published after this "Year
  2024" table** — recheck before this touches a real payroll month, and
  again whenever a new tax year's rates are published.
- **PAYE must be calculated with per-band rounding**, not
  sum-then-round — the two disagree by a cent on some inputs, and this
  project's whole merge-validation phase depends on TCS OS and the ERP
  producing identical payslips line-by-line. See `docs/DESIGN.md`.
- **Not yet built, flagged for confirmation before building**: overtime
  income for a qualifying junior employee (income ≤ GHS 800/month per
  GRA's own completion notes) may need a flat concessionary tax rate
  instead of graduated PAYE; bonus income reportedly has an unmodeled
  flat 5% final-tax treatment. Neither is implemented in the ERP or TCS
  OS pending confirmation from TCS's accountant or GRA directly.
- Rates and bands are stored as **editable database rows**, seeded by
  idempotent data migrations, never hardcoded constants — a correction
  or an annual update should be a data change, not a code deploy.

## Checklist — before the HR/Finance merge goes anywhere near real payroll

- [x] Real GRA 2024 PAYE bands seeded in TCS OS, matching the ERP
- [x] Statutory rates (0.5% / 13% / 5%) seeded, matching the ERP
- [x] `calculate_payslip()` verified against a real ERP payslip
      (Emmanuel Ansah, basic 6,500) to the cent, including the
      per-band PAYE rounding order
- [x] `admin.py` registration for the HR models
- [ ] A proper test suite (not just hand-verification) built from the
      Emmanuel Ansah payslip as ground truth
- [ ] Views/URLs for the actual payroll workflow (create run, generate
      payslips, Draft → Ready for Review → Posted)
- [ ] A real payroll month run in both TCS OS and the ERP in parallel,
      compared line-by-line, before either system is trusted alone
- [ ] Confirm the SSNIT/Tier 2 split above against an official SSNIT
      source (currently confirmed only against TCS's own stated
      practice — carried over as-is from the ERP's own unresolved
      checklist item, not newly discovered here)

## Data safety

- This project's Supabase instance is dedicated to TCS OS. It must
  never be confused with, or connected to, the separate standalone TCS
  ERP's own Supabase project (a different project entirely, holding the
  ERP's test payroll/finance data) or the Wilelik ERP's project (Eyram's
  family business, unrelated to TCS). Three genuinely separate Supabase
  projects exist across Eyram's work; keep them that way until the ERP
  merge's Phase 3 (data migration) deliberately moves specific config
  data across, on purpose, via a reviewed management command — never by
  pointing one project's connection string at another's data.

## Branding

- Every module's UI follows **TCS Brand Guidelines v1.0** precisely —
  see `docs/DESIGN.md`'s Branding section for the summary and two
  flagged inconsistencies in the source document itself (a stray "deep
  pink" reference on the Usage Proportion page, and Aqua Veil/Deep Teal
  Shadow sharing identical color values under different names). Neither
  of those two is a real instruction to follow; both are worth a
  one-line confirmation with the designer, not a decision made silently
  in code.
- A generated official document (a payslip, an Appointment Letter, a
  Contract) must be opened and visually inspected before being
  considered done — see the lesson below.

## Solo development

- Eyram is the only developer, building primarily via **Claude Code
  with conversational prompts**, not upfront specs. Keep changes scoped
  and reviewable — one module or feature at a time.
- Eyram is early in his data science / programming learning curve.
  This doesn't change the standard code is held to, but the *why* behind
  a decision is worth keeping in the docs, not assumed to carry in one
  person's head.

## Lessons that cost something once — don't repeat them

- **A statutory or regulatory rate structure must be verified against
  an authoritative source or a working reference implementation before
  being changed** — not against a conversational restatement that
  hasn't itself been checked. See `docs/DESIGN.md`'s Tier 2 incident.
- **"The file exists, has the right size, and has the right magic
  bytes" is not evidence a generated binary document is correct** — only
  opening and looking at it is. The ERP's document-generation feature
  shipped a genuinely blank PDF once, past exactly this kind of
  header-only check.
- **Bulk admin actions (`queryset.update()`) bypass `Model.save()`** —
  any save()-time side effect (a reference-number assignment, an audit
  trigger, a derived-field recompute) silently never fires. Iterate and
  call `.save()` per row for any bulk action that needs those side
  effects.
- **A batch send (email, or anything else with one bad item, is unsafe
  as all-or-nothing.** Validate/filter before batching, so one bad
  recipient/record can't take an entire otherwise-good batch down with
  it.
- **File upload validation belongs at three layers** — client (UX only,
  trivially bypassed), the serializer/API layer (rejects most bad
  input), and the storage bucket itself (the only layer a client can't
  bypass by lying about its own request).