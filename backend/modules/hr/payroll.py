"""Payroll calculation — a port of the TCS ERP's create_payslip logic.

calculate_payslip() is deliberately a pure function: given an Employee, a
PayrollRun (used only to resolve the pay period — see _period_end()) and
this month's variable inputs (overtime, allowances, fines, IOU), it reads
whatever DB config is effective for that period and returns a plain dict —
it does NOT create or touch a Payslip row. Persisting the result is a
separate step (`Payslip.objects.create(payroll_run=..., employee=...,
**calculate_payslip(...))`), left to the run-generation view/command that
isn't built yet.

**Employee vs. employer contributions — the actual Ghana scheme, National
Pensions Act Act 766 (corrected 2026-09-23; an earlier pass on this same
date had this wrong in the other direction — no employer Tier 2, an
inflated employee Tier 2 — see git history for the reverted attempt):**

18.5% of basic is mandatory in total:

- The employee pays **5.5% total, BOTH deducted from net pay**:
  0.5% to SSNIT Tier 1 (`ssnit`) + 5% to Tier 2, a private trustee
  (`tier2`). PAYE, fines, and IOU are the only other things ever deducted.
- The employer pays **13% total, entirely to Tier 1** (`ssnit_employer`) —
  employer cost only, never reduces the employee's net pay. There is
  **no employer Tier 2 contribution** — Tier 2 is 100% employee-funded.
- Tier 1 total = 13.5% (13% employer + 0.5% employee). Tier 2 total = 5%
  (all employee).

Design decisions made while porting that the given spec didn't spell out
explicitly — flagged here rather than assumed silently:

1. Employee.basic_salary is NOT used for calculation. The authoritative
   figure is the EmployeePayConfig effective for the employee as of the
   PayrollRun's period, and only if that config's approval_status is
   "approved" — see _get_effective_pay_config(). Employee.basic_salary is
   display/reference only (see that model's docstring). If this is wrong —
   if the ERP actually calculates off Employee.basic_salary directly and
   EmployeePayConfig is something else — this is the one thing in this
   port most worth double-checking against the real ERP source.

2. ssnit (employee Tier 1) and ssnit_employer (employer Tier 1) are both
   gated on pays_ssnit; tier2 (employee, the only Tier 2 side that exists)
   is gated on pays_tier2. Confirm against the ERP if an employee can
   genuinely be excluded from one tier but not the other.

3. total_allowances (which feeds gross_salary) sums ALL allowances,
   taxable or not — allowances are still earnings paid to the employee.
   Only the PAYE base uses the taxable subset.

4. PAYEBand.lower_bound/upper_bound are read as cumulative, absolute
   income thresholds (not per-band widths) — see that model's docstring.

5. taxable_income = basic + overtime + taxable_allowances - ssnit - tier2
   — BOTH employee-side statutory deductions come off before PAYE runs.
   total_deductions/net_pay = paye + ssnit + tier2 + fines + iou.
   ssnit_employer is excluded from both — it's an employer cost, never
   deducted from the employee's own pay.

6. Every component is rounded to 2dp the moment it's computed (not just
   once, at the end, on the final dict) — found necessary during
   verification: rounding only at the end let total_deductions/net_pay be
   built from full-precision intermediates that didn't match the rounded
   line items actually shown on the payslip, off by a cent in some cases.
   A real payslip must tie out to its own printed lines, so ssnit/tier2/
   ssnit_employer/paye/fines/iou/allowances are each rounded as soon as
   they exist, and paye_base/total_deductions/net_pay are built entirely
   from those already-rounded values.

7. Within _calculate_paye() specifically, "round as soon as computed" means
   rounding EACH BAND'S contribution to 2dp before adding it to the running
   PAYE total — matching the ERP reference create_payslip()'s
   `v_tax := v_tax + round(v_band_amount * v_band.rate / 100, 2)` exactly.
   This is NOT equivalent to summing all bands at full precision and
   rounding once at the end (note 6's pattern) — confirmed via a real,
   reproducible one-cent drift at basic 6,500 (1,134.12 summed-then-rounded
   vs. the ERP's ground-truth 1,134.13, per-band rounded). Cross-checked
   against a real ERP payslip (Emmanuel Ansah, Sept 2026): basic 6,500 ->
   PAYE 1,134.13, ssnit 32.50, tier2 325.00, net_pay 5,008.37, all matched
   exactly once per-band rounding was applied.

No SSNIT/Tier2/PAYE figures are hardcoded anywhere in this file — they all
come from StatutoryRate/PAYEBand rows. Nothing seeds those tables with real
numbers as part of this port; load the actual current GRA/SSNIT figures via
admin (or a future data migration) before running real payroll.
"""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

from django.db.models import Q

from .models import AllowanceType, Employee, EmployeePayConfig, PAYEBand, PayrollRun, StatutoryRate

TWO_PLACES = Decimal("0.01")


class PayrollConfigError(Exception):
    """Raised when calculate_payslip() can't find the config it needs for
    the requested period — an unapproved/missing EmployeePayConfig, or no
    PAYEBand/StatutoryRate rows effective by that date. Deliberately never
    silently falls back to a zero or a stale value for money calculations."""


def _to_decimal(value) -> Decimal:
    """Defensive coercion for caller-supplied numeric inputs (overtime
    hours/rate, allowance amounts, fines, IOU) — never Decimal(float)
    directly, which carries binary-float imprecision into currency math
    (e.g. Decimal(0.1) != Decimal("0.1"))."""
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _period_end(payroll_run: PayrollRun):
    """The last calendar day of the run's month/year — the date used to
    resolve "what config/rates/bands were effective during this run"."""
    last_day = calendar.monthrange(payroll_run.year, payroll_run.month)[1]
    return date(payroll_run.year, payroll_run.month, last_day)


def _get_effective_pay_config(employee: Employee, payroll_run: PayrollRun) -> EmployeePayConfig:
    period_end = _period_end(payroll_run)
    config = (
        EmployeePayConfig.objects.filter(
            employee=employee,
            approval_status="approved",
            effective_from__lte=period_end,
        )
        # effective_to is null (open-ended) OR on/after the period being calculated.
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=period_end))
        .order_by("-effective_from")
        .first()
    )
    if config is None:
        raise PayrollConfigError(
            f"No approved EmployeePayConfig is effective for {employee} during "
            f"{payroll_run.month:02d}/{payroll_run.year}. Approve a pay config covering that "
            f"period before running payroll for this employee."
        )
    return config


def _get_effective_statutory_rate(as_of_date) -> StatutoryRate:
    rate = (
        StatutoryRate.objects.filter(effective_from__lte=as_of_date)
        .order_by("-effective_from")
        .first()
    )
    if rate is None:
        raise PayrollConfigError(
            f"No StatutoryRate is effective on/before {as_of_date.isoformat()}. "
            f"Add one via admin before running payroll."
        )
    return rate


def _get_effective_paye_bands(as_of_date) -> List[PAYEBand]:
    """Resolves "the current PAYE table" as one atomic set: the latest
    effective_from on/before as_of_date, then every band sharing that exact
    date, ordered by band_order — see PAYEBand's docstring for why a whole
    table is versioned together rather than band-by-band."""
    latest_date = (
        PAYEBand.objects.filter(effective_from__lte=as_of_date)
        .order_by("-effective_from")
        .values_list("effective_from", flat=True)
        .first()
    )
    if latest_date is None:
        raise PayrollConfigError(
            f"No PAYEBand rows are effective on/before {as_of_date.isoformat()}. "
            f"Load the PAYE table via admin before running payroll."
        )
    return list(PAYEBand.objects.filter(effective_from=latest_date).order_by("band_order"))


def _calculate_paye(taxable_income: Decimal, bands: List[PAYEBand]) -> Decimal:
    """Standard graduated/marginal-rate calculation over cumulative,
    absolute band thresholds (see PAYEBand's docstring). Each band taxes
    only the slice of income that falls within its own [lower_bound,
    upper_bound) range at that band's rate.

    Rounding order matters and must match the ERP's reference
    create_payslip() exactly: `v_tax := v_tax + round(v_band_amount *
    v_band.rate / 100, 2)` — each band's OWN contribution is rounded to 2dp
    the instant it's computed, then added to the running total; the running
    total itself is never re-rounded. This is intentionally NOT the same as
    summing every band at full precision and rounding once at the end —
    confirmed via a real, reproducible one-cent drift at basic 6,500
    (1,134.12 summed-then-rounded vs. the ERP's 1,134.13 rounded-per-band).
    Per-band rounding is the one place in this module where "round as soon
    as a number is computed" (the discipline the rest of calculate_payslip()
    already follows for ssnit/tier2/etc.) means rounding INSIDE a loop
    rather than once on a single flat calculation."""
    if taxable_income <= 0:
        return Decimal("0.00")

    tax = Decimal("0.00")
    for band in bands:
        if taxable_income <= band.lower_bound:
            continue  # income hasn't reached this band at all
        band_ceiling = band.upper_bound if band.upper_bound is not None else taxable_income
        amount_in_band = min(taxable_income, band_ceiling) - band.lower_bound
        if amount_in_band > 0:
            tax += _money(amount_in_band * band.rate)
    return tax


def calculate_payslip(
    employee: Employee,
    payroll_run: PayrollRun,
    overtime_hours: Decimal = Decimal("0"),
    overtime_rate: Decimal = Decimal("0"),
    allowances: Optional[List[Dict]] = None,
    fines: Decimal = Decimal("0"),
    iou: Decimal = Decimal("0"),
) -> Dict:
    """Returns a dict shaped exactly like Payslip's calculated fields:
    {
        'basic_salary': ..., 'overtime_pay': ..., 'total_allowances': ...,
        'gross_salary': ..., 'ssnit': ..., 'tier2': ..., 'paye': ...,
        'ssnit_employer': ..., 'fines': ..., 'iou': ...,
        'total_deductions': ..., 'net_pay': ...,
    }
    Every value is a Decimal rounded to 2dp (ROUND_HALF_UP).

    Mirrors the ERP's create_payslip logic. Plainly stated (corrected
    2026-09-23, National Pensions Act Act 766): the employee pays 5.5%
    total — 0.5% SSNIT Tier 1 + 5% Tier 2 — and BOTH are deducted from net
    pay, alongside PAYE, fines, and IOU. The employer separately pays 13%,
    entirely to Tier 1 (SSNIT only, no employer Tier 2 exists), tracked as
    employer cost only (`ssnit_employer`) — never reduces net pay. Grand
    total mandatory contribution: 18.5% of basic (13.5% to Tier 1, 5% to
    Tier 2).

    - SSNIT Tier 1 employee share (0.5%) on basic only, if pays_ssnit —
      deducted from net pay
    - Tier 2 (5%, 100% employee-funded) on basic only, if pays_tier2 —
      deducted from net pay
    - SSNIT Tier 1 employer share (13%) on basic only, if pays_ssnit —
      calculated separately, employer cost only, never subtracted from net pay
    - PAYE: graduated bands on (basic + overtime + taxable_allowances - ssnit - tier2),
      only if pays_paye is True
    - Fines and IOU as flat deductions

    Raises PayrollConfigError if no approved EmployeePayConfig, StatutoryRate,
    or PAYEBand set is effective for the run's period — see the module
    docstring's design-decision notes for what's assumed vs. flagged.
    """
    # Every component is rounded to money precision (_money()) THE MOMENT
    # it's computed, not just once at the end on the final dict — otherwise
    # total_deductions/net_pay get built from full-precision intermediates
    # that don't match the rounded line items the payslip actually shows,
    # and the two silently disagree by a cent here and there (found during
    # verification: 3000 basic produced net_pay=2448.13 but gross(3000.00)
    # - the displayed, rounded ssnit+tier2+paye(15.00+150.00+386.88=551.88)
    # = 2448.12 — a real payslip should always tie out to its own printed
    # lines). Rounding early guarantees that: every later sum is built from
    # the same numbers that end up in the return dict, so the final _money()
    # calls below are now just idempotent defensiveness, not load-bearing.
    overtime_hours = _to_decimal(overtime_hours)
    overtime_rate = _to_decimal(overtime_rate)
    fines = _money(_to_decimal(fines))
    iou = _money(_to_decimal(iou))
    allowances = allowances or []

    period_end = _period_end(payroll_run)
    pay_config = _get_effective_pay_config(employee, payroll_run)
    statutory_rate = _get_effective_statutory_rate(period_end)

    basic_salary = _money(pay_config.basic_salary)
    overtime_pay = _money(overtime_hours * overtime_rate)

    # One query for every AllowanceType referenced, rather than one per
    # allowance line — matters once a payslip carries several allowances.
    type_ids = [a["type_id"] for a in allowances if a.get("type_id") is not None]
    taxable_by_type = dict(
        AllowanceType.objects.filter(pk__in=type_ids).values_list("pk", "taxable")
    )

    total_allowances = Decimal("0")
    taxable_allowances = Decimal("0")
    for allowance in allowances:
        amount = _money(_to_decimal(allowance.get("amount")))
        total_allowances += amount
        if taxable_by_type.get(allowance.get("type_id"), False):
            taxable_allowances += amount

    gross_salary = _money(basic_salary + overtime_pay + total_allowances)

    # Employee-side statutory deductions — BOTH come off net pay. See module
    # docstring: SSNIT Tier 1 is 0.5% employee / 13% employer; Tier 2 is 5%,
    # 100% employee-funded (no employer side exists for it at all).
    ssnit = _money(basic_salary * statutory_rate.ssnit_employee_pct) if pay_config.pays_ssnit else Decimal("0.00")
    tier2 = _money(basic_salary * statutory_rate.tier2_employee_pct) if pay_config.pays_tier2 else Decimal("0.00")
    # Employer cost only, never an employee deduction — see module docstring note 2.
    ssnit_employer = (
        _money(basic_salary * statutory_rate.ssnit_employer_pct) if pay_config.pays_ssnit else Decimal("0.00")
    )

    if pay_config.pays_paye:
        # Both employee-side statutory deductions (already rounded above)
        # come off before PAYE runs, so PAYE is calculated on the same
        # taxable income a manual audit of the payslip would arrive at.
        paye_base = basic_salary + overtime_pay + taxable_allowances - ssnit - tier2
        paye_base = max(paye_base, Decimal("0.00"))  # defensive floor; see module docstring
        bands = _get_effective_paye_bands(period_end)
        paye = _money(_calculate_paye(paye_base, bands))
    else:
        paye = Decimal("0.00")

    # Net-pay deductions: PAYE + the employee's SSNIT Tier 1 share + the
    # employee's Tier 2 share + fines + IOU. ssnit_employer is deliberately
    # excluded — an employer cost, never subtracted from net pay.
    total_deductions = _money(paye + ssnit + tier2 + fines + iou)
    net_pay = _money(gross_salary - total_deductions)

    return {
        "basic_salary": basic_salary,
        "overtime_pay": overtime_pay,
        "total_allowances": total_allowances,
        "gross_salary": gross_salary,
        "ssnit": ssnit,
        "tier2": tier2,
        "paye": paye,
        "ssnit_employer": ssnit_employer,
        "fines": fines,
        "iou": iou,
        "total_deductions": total_deductions,
        "net_pay": net_pay,
    }
