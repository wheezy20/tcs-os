# TCS OS — Design Conventions

This file is the source of truth for architectural, schema, and branding
conventions that apply across every module. `CLAUDE.md` points here
rather than restating them. A module's own `docs/<module>/02-stack-and-
schema.md` covers what's specific to that module only — a convention
used by more than one module, or one every new module should follow,
belongs here instead.

## Architecture & RBAC

- **One Django project, one app per module, one shared User model.**
  `backend/admissions/`, `backend/hr/`, `backend/finance/`, etc. are all
  apps inside the same project — never a separate backend or a separate
  User table per module. See `docs/shared-stack.md`.
- **Django owns all database access.** Supabase is used purely as
  hosted Postgres + Storage. Supabase's own Auth/PostgREST/RLS layer is
  deliberately not enabled — Django connects with full access and does
  its own permission checks in code. Don't reach for RLS or Supabase
  Auth for a TCS OS feature; the access-control pattern is Django
  Groups/Permissions plus, where finer scoping is needed, a live
  queryset filter (see Grade-band scoping below).
- **RBAC is Django Groups + Permissions, checked in code, not a
  database-level policy.** A permission that shouldn't be auto-granted
  (health data access, bulk email send, financial approval) gets its own
  `Meta.permissions` entry and is bundled into a named Group by an
  idempotent data migration — never auto-assigned to anyone. Admissions'
  `Administration` group (bundles every non-destructive admissions
  permission) and its three Coordinator groups are the reference
  implementation; a new module needing similar scoping should follow the
  same shape rather than inventing a new permission-modeling approach.
- **Live queryset scoping over stored per-row assignment**, when access
  needs to be narrower than a flat Group. Admissions' grade-band
  coordinator scoping (`GradeBandScopedAdmin`, `scoped_grades_for()`)
  filters on the *current* value of a live column (`year_group_applied_
  for`), not a snapshot taken at creation — so a record automatically
  resolves to the right scope if the underlying field changes later,
  with no backfill step. Reach for this pattern before adding a stored
  "assigned_to" or "band" column that could drift from the data it's
  supposed to reflect.

## Effective-dated config, not mutable single rows

Where a value needs to be reconstructed *as of* a past date — pay
configuration, a statutory rate, a fee schedule — the row is
effective-dated (`effective_from`/`effective_to`) rather than updated in
place. A record generated against that config (a payslip, an invoice)
references the config row that was active at the time, so a later
correction never silently rewrites history. `hr.EmployeePayConfig` and
`hr.StatutoryRate` follow this; any future module with a similar "this
changes over time but past records must stay frozen" need should too.

Current-state facts (a person's name, phone, position) are not
effective-dated — that pattern is only for values a future record needs
to reconstruct as-of a past moment, not everything that can change.

## Idempotent seed data via data migrations

Reference data that must exist in every environment and isn't
branch/tenant-scoped (a permission-bundling Group, statutory rates, a
reference band table) is seeded by a data migration using `get_or_create`
or an equivalent idempotent pattern keyed on the row's own natural
unique field — never a bare `create()` that would duplicate on a second
`migrate` run. `admissions`'s `0017_administration_group` and
`hr`'s `0002_seed_statutory_data` are the reference implementations.
Verify idempotency directly (run the migration two or three times
against a real or throwaway DB) before trusting it, not just by reading
the code.

## Public-token trust model

Any endpoint that lets someone act without a login (a resume-later
draft, an unsubscribe link, an onboarding form) uses the same shape:
a cryptographically random token (not a guessable ID) *is* the entire
access control, checked server-side, with no secondary identity check
layered on top. Admissions' `Offer`/`ApplicationDraft` tokens and the
onboarding-form token pattern being ported from the ERP (see the
Employee-generated-documents section below) are the reference. A route
built this way needs: single-use or expiry enforcement (row-locked at
the moment of use if single-use matters), and a clear boundary on what
that token can and cannot touch — it should never be a backdoor into
anything beyond the one record it was minted for.

## Payroll & statutory reference data (HR module)

**The confirmed-correct Ghana statutory scheme** (National Pensions Act,
Act 766) — get this right before touching any payroll code, it has
already been gotten wrong once in this project's history (see the
incident writeup below):

- **Employee pays 5.5% total**, entirely deducted from net pay:
  0.5% to SSNIT Tier 1, 5% to Tier 2 (a private trustee, e.g. Glico).
- **Employer pays 13% total**, entirely employer-side, never touching
  net pay: all 13% to SSNIT Tier 1. **The employer contributes nothing
  to Tier 2** in this scheme.
- Tier 1 total = 13.5% (13% employer + 0.5% employee). Tier 2 total =
  5% (entirely employee-funded). Grand total = 18.5%.
- `taxable_income = basic + overtime + taxable_allowances −
  ssnit(employee) − tier2(employee)`, then graduated PAYE runs on that
  figure. `total_deductions = paye + ssnit(employee) + tier2(employee) +
  fines + iou`. Neither SSNIT nor Tier 2 has an employer-side figure that
  ever reduces net pay.

**The Tier 2 incident (2026-09-23/24) — a mistake, corrected, kept here
as a permanent record so it isn't repeated.** Mid-session, based on an
unverified conversational restatement, both the standalone TCS ERP's
`create_payslip()` and this project's `hr.calculate_payslip()` were
changed to a *different, wrong* scheme (SSNIT employer-only 13%, Tier 2
split 5% employer / 0.5% employee). This was built, migrated in the ERP
(caught before commit), and briefly verified against that wrong premise.
The ERP's *original* `create_payslip()` was already correct — the "fix"
was reverted; TCS OS's models and `calculate_payslip()` were reverted to
match. No real payroll had run on the incorrect version in either system
(the ERP has only ever run test payroll), so no back-pay or compliance
issue resulted. **Lesson: verify a statutory rate structure against an
authoritative source or a working reference implementation before
changing it — not against a conversational restatement that hasn't
itself been checked.**

**Real GRA 2024 PAYE bands** (effective 2025-01-01) — GRA's own published
table is internally inconsistent between its cumulative-sum column and
its literal band label for the top two bands (arithmetic sums to
50,416.67, but the label reads "Exceeding 50,000.00"); both the ERP and
TCS OS use the literal **50,000.00** cutoff:

| Band | Lower bound | Upper bound | Rate |
|---|---|---|---|
| 1 | 0 | 490.00 | 0% |
| 2 | 490.00 | 600.00 | 5% |
| 3 | 600.00 | 730.00 | 10% |
| 4 | 730.00 | 3,896.67 | 17.5% |
| 5 | 3,896.67 | 19,896.67 | 25% |
| 6 | 19,896.67 | 50,000.00 | 30% |
| 7 | 50,000.00 | ∞ | 35% |

**PAYE rounding order matters and must match the ERP exactly**, since
the two systems' payslips are compared line-by-line during the merge
validation phase. Round **each band's own tax contribution** to 2dp the
moment it's computed, then add the already-rounded figure to the running
total — not sum-full-precision-then-round-once. These two approaches
disagree by a cent whenever a band's raw contribution has a fractional
cent (confirmed on a real case: basic 6,500 gives PAYE 1,134.12 under
sum-then-round vs. the ERP's correct 1,134.13 under per-band rounding).

**Ground truth reference payslip**, used to verify the port:
Emmanuel Ansah, Head Teacher, Administration, basic salary 6,500,
September 2026 — SSNIT (employee) 32.50, Tier 2 (employee) 325.00, PAYE
1,134.13, total deductions 1,491.63, taxable income 6,142.50, **net pay
5,008.37**, SSNIT (employer) 845.00, total cost of employment 7,345.00.
`hr.calculate_payslip()` reproduces every one of these to the cent.

## Employee-generated documents (pattern to port from the ERP)

The ERP's `employee_generated_documents` design (Appointment Letter,
Probation Letter, Contract-Teaching/Non-Teaching) is one generalized
lifecycle table with a `document_kind` column, not one table per
document type — all three share identical mechanics (Draft → Issued →
Superseded, old versions archived not deleted, at most one currently-
Issued document per employee per kind). Port this shape rather than the
ERP's specific rendering mechanism (html2canvas + jsPDF, a client-side,
React-specific solution) — the Django port should render server-side
(e.g. WeasyPrint or a similar HTML-to-PDF library) rather than
reproducing the ERP's client-side approach, since that mechanism exists
only because the ERP is a browser SPA with no server-side rendering
step. Full lifecycle/versioning rules are documented here once this is
actually built in TCS OS (Merge Phase 1, Session 7 in `PLAN.md`).

## Branding

Canonical source: **TCS Brand Guidelines v1.0** (30 May 2026, designed
by Martin Kpogo). Every module's UI — admin dashboards, public forms,
generated documents (payslips, letters, contracts) — follows this
precisely, not placeholder design. `docs/admissions/brand-tokens.md` is
that module's own quick-reference copy of the same source; the summary
below is the system-wide version any new module should start from.

### Colors

| Name | Hex | Role |
|---|---|---|
| Deep Jungle Green | `#005E61` | Primary — wordmark on light backgrounds; this project's UI `--primary` |
| Mint Leaf | `#11B87A` | Logo laurel on light/white backgrounds only (contrast-safe alt to lime) |
| Vivid Lime Green | `#ADF802` | Signature accent, primarily on dark backgrounds (incl. the logo laurel there) |
| Cayenne Red / Orange | `#F15E00` | Signature accent — flame/crest color |
| Deep Teal Shadow | `#0B4C50` | Supporting dark surface |
| Ocean Field | `#2F8C8F` | Supporting mid-tone |
| Jungle Mist | `#E6F3F3` | Light neutral background |

Extended palette (lower-frequency use): Lime Mist `#F3FFD1`, Burnt Ember
`#7A2A00`, Electric Bloom `#D6FF66`, Hot Ember `#E65A00`, Neon Pulse
`#88D400`, Flame Glow `#FFA833`, Forest Green `#3F6A00`, Ember Mist
`#FFE2CF`.

**Two things worth flagging, found by reading the actual source PDF
rather than trusting a prior summary:** the guide's own "Usage
Proportion" page names "deep pink" as the primary brand color at 26%
usage — deep pink appears nowhere else in the document and isn't part of
this palette at all; it's almost certainly unedited template boilerplate
left over from whatever generic guide template this was built from, not
a real instruction. Treat Deep Jungle Green as primary (consistent with
every other page) and disregard that one line. Separately, **Aqua Veil**
and **Deep Teal Shadow** are listed with identical CMYK/RGB/HEX values
but different Pantone codes (`2725 C` vs `7476 C`) — either the same
color under two names, or a copy-paste error in the source document.
Worth a one-line confirmation with the designer before treating them as
two distinct colors in code; until then, treat `#0B4C50` as one color
(Deep Teal Shadow) and don't build a separate `--aqua-veil` token.

### Logo usage

- **Dark backgrounds** → the lime-green (`#ADF802`) laurel variant.
- **White/light backgrounds** → the Mint Leaf (`#11B87A`) laurel variant.
- Never stretch, distort, recolor, add effects/outlines, rotate, or
  place on an unapproved background color.
- Minimum size: full logo/wordmark 80px height, symbol alone 20px,
  favicon 16px.
- Clear space: ~1/4 of the symbol's height/width around it.
- For any co-branding/partnership use: black or white logo only, the
  symbol's diamond shape as the divider between two logos.

### Typography

- **Cinzel** (serif) — headings/titles/display text. Weights: Bold,
  Regular. Heading setting: Title Case, tracking -30, leading 90% of
  point size.
- **DM Sans** (sans-serif) — body/forms/captions. Weights: 18 Regular
  (body, leading 130%, tracking -10), 24 Medium (subheading, Sentence
  Case, leading 100%, tracking -20).
- Both are Google Fonts — `<link>`, no local font files.
- Always align left; never set headings in all-lowercase.

### Tone of voice

Four traits, all four together, not any one in isolation: **Assured &
Understated** (calm confidence, no over-promising, no "loud" marketing
language), **Reverent & Grounded** (respectful of the school's Christian
heritage, never overly casual), **Cultivated & Refined** (articulate,
polished, emphasizes character and poise, not just grades), **Legacy-
Oriented & Relational** (speaks to parents as fellow stewards of a
child's future, inclusive, generational-partnership framing).

Personality traits carried into visual/UX decisions specifically:
**Nurturing** → generous whitespace, containment, photography favoring
guidance/encouragement over staged achievement. **Structured** → visible
grids, alignment, consistent hierarchy — TCS OS's own admin-dashboard
conventions already do this. **Dependable** → visual consistency,
balanced/stable layouts. **Sophisticated** → restraint over decorative
effects. **Approachable** → authentic, non-staged imagery where photos
are used at all.

**Tagline: "Every child is a treasure."** — usable across the product
wherever a short, brand-voiced line fits (a footer, an empty state, an
email sign-off). Writing rule from the guide, worth applying to any
system-generated copy (emails, notifications, error messages): say less,
mean more; if a parent has to reread it to understand it, it's too
complex; ground every message in lived experience, not abstraction.

### Photography (when a module uses real photos, not just icons/graphics)

Three principles: **Learning in Motion** (the process of learning, not
just a posed activity), **Character in Context** (values shown through
real interactions, not staged), **Warmth and Authenticity** (genuine
expressions, natural environments). Never: obvious stock imagery, staged
photography, low-res images, sombre/dark imagery.