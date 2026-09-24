"""Coverage for hr/payroll.py's calculate_payslip() and the config it reads
(EmployeePayConfig effective-dating, PAYEBand/StatutoryRate as seeded by
migration 0002). Anchored on a real, independently-verified ERP payslip —
Emmanuel Ansah, Head Teacher, September 2026 (basic 6,500) — see
docs/DESIGN.md's payroll section for the source of that ground truth.

Every non-ground-truth expected value below was computed independently of
calculate_payslip() itself (a separate, hand-written per-band walk — see
the comments on each test), not derived by calling the function under test
and trusting its own output.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.test import TestCase

from .models import Employee, EmployeePayConfig, PAYEBand, PayrollRun, StatutoryRate
from .payroll import PayrollConfigError, calculate_payslip

STATUTORY_EFFECTIVE_FROM = date(2025, 1, 1)  # matches migration 0002's seeded rows


def _make_payroll_run(month=9, year=2026, branch="Main"):
    return PayrollRun.objects.create(branch=branch, month=month, year=year, status="draft")


def _make_employee(employee_number, first_name="Test", surname="Employee"):
    return Employee.objects.create(
        employee_number=employee_number,
        first_name=first_name,
        surname=surname,
        basic_salary=Decimal("0.00"),  # display-only; irrelevant to payroll — see Employee's docstring
        employment_status="active",
    )


def _make_pay_config(
    employee,
    basic_salary,
    effective_from=STATUTORY_EFFECTIVE_FROM,
    effective_to=None,
    pays_ssnit=True,
    pays_tier2=True,
    pays_paye=True,
    approval_status="approved",
):
    return EmployeePayConfig.objects.create(
        employee=employee,
        basic_salary=basic_salary,
        pays_ssnit=pays_ssnit,
        pays_tier2=pays_tier2,
        pays_paye=pays_paye,
        effective_from=effective_from,
        effective_to=effective_to,
        approval_status=approval_status,
    )


class EmmanuelAnsahGroundTruthTests(TestCase):
    """The real ERP payslip this whole port is verified against: Emmanuel
    Ansah, Head Teacher, September 2026, basic 6,500, all three pays_*
    flags True, no overtime, no allowances. See docs/DESIGN.md."""

    def setUp(self):
        self.payroll_run = _make_payroll_run()
        self.employee = _make_employee("EMP-ANSAH-001", "Emmanuel", "Ansah")
        _make_pay_config(self.employee, Decimal("6500.00"))

    def test_every_field_matches_the_real_erp_payslip_to_the_cent(self):
        result = calculate_payslip(self.employee, self.payroll_run)

        self.assertEqual(result["basic_salary"], Decimal("6500.00"))
        self.assertEqual(result["overtime_pay"], Decimal("0.00"))
        self.assertEqual(result["total_allowances"], Decimal("0.00"))
        self.assertEqual(result["gross_salary"], Decimal("6500.00"))
        self.assertEqual(result["ssnit"], Decimal("32.50"))
        self.assertEqual(result["tier2"], Decimal("325.00"))
        self.assertEqual(result["ssnit_employer"], Decimal("845.00"))
        self.assertEqual(result["paye"], Decimal("1134.13"))
        self.assertEqual(result["fines"], Decimal("0.00"))
        self.assertEqual(result["iou"], Decimal("0.00"))
        self.assertEqual(result["total_deductions"], Decimal("1491.63"))
        self.assertEqual(result["net_pay"], Decimal("5008.37"))

        # taxable_income isn't a key in the returned dict, but it's a fixed
        # function of basic/ssnit/tier2 (see calculate_payslip's docstring,
        # note 5) — check it lands on the ground-truth figure too.
        taxable_income = result["basic_salary"] - result["ssnit"] - result["tier2"]
        self.assertEqual(taxable_income, Decimal("6142.50"))


class PaysFlagIndependenceTests(TestCase):
    """Each pays_* flag gates exactly its own deduction(s) and nothing
    else. Expected figures computed independently below (not by calling
    calculate_payslip against itself) — see the comment on each test for
    the arithmetic."""

    def setUp(self):
        self.payroll_run = _make_payroll_run()

    def test_pays_ssnit_false_zeroes_ssnit_and_ssnit_employer_only(self):
        # basic 6500, ssnit off, tier2 on, paye on.
        # ssnit = 0.00, ssnit_employer = 0.00 (both gated on pays_ssnit).
        # tier2 = 6500 * 0.05 = 325.00 (unaffected).
        # taxable_income = 6500 - 0 - 325 = 6175.00.
        # PAYE per-band walk on 6175.00: band2 5.50 + band3 13.00 +
        # band4 554.17 + band5 569.58 = 1142.25.
        employee = _make_employee("EMP-NOSSNIT-001")
        _make_pay_config(employee, Decimal("6500.00"), pays_ssnit=False, pays_tier2=True, pays_paye=True)

        result = calculate_payslip(employee, self.payroll_run)

        self.assertEqual(result["ssnit"], Decimal("0.00"))
        self.assertEqual(result["ssnit_employer"], Decimal("0.00"))
        self.assertEqual(result["tier2"], Decimal("325.00"))
        self.assertEqual(result["paye"], Decimal("1142.25"))
        self.assertEqual(result["total_deductions"], Decimal("1467.25"))
        self.assertEqual(result["net_pay"], Decimal("5032.75"))

    def test_pays_tier2_false_zeroes_tier2_only(self):
        # basic 6500, ssnit on, tier2 off, paye on.
        # ssnit = 32.50, ssnit_employer = 845.00 (unaffected — gated on
        # pays_ssnit, not pays_tier2). tier2 = 0.00.
        # taxable_income = 6500 - 32.50 - 0 = 6467.50.
        # PAYE per-band walk on 6467.50: band2 5.50 + band3 13.00 +
        # band4 554.17 + band5 (6467.50-3896.67)*0.25=642.7075 -> 642.71
        # = 1215.38.
        employee = _make_employee("EMP-NOTIER2-001")
        _make_pay_config(employee, Decimal("6500.00"), pays_ssnit=True, pays_tier2=False, pays_paye=True)

        result = calculate_payslip(employee, self.payroll_run)

        self.assertEqual(result["ssnit"], Decimal("32.50"))
        self.assertEqual(result["ssnit_employer"], Decimal("845.00"))
        self.assertEqual(result["tier2"], Decimal("0.00"))
        self.assertEqual(result["paye"], Decimal("1215.38"))
        self.assertEqual(result["total_deductions"], Decimal("1247.88"))
        self.assertEqual(result["net_pay"], Decimal("5252.12"))

    def test_pays_paye_false_zeroes_paye_regardless_of_taxable_income(self):
        # basic 6500, ssnit on, tier2 on, paye off.
        # ssnit = 32.50, tier2 = 325.00, ssnit_employer = 845.00 (all
        # unaffected by pays_paye). paye = 0.00 outright — no band lookup
        # even happens (see calculate_payslip's `if pay_config.pays_paye`).
        employee = _make_employee("EMP-NOPAYE-001")
        _make_pay_config(employee, Decimal("6500.00"), pays_ssnit=True, pays_tier2=True, pays_paye=False)

        result = calculate_payslip(employee, self.payroll_run)

        self.assertEqual(result["ssnit"], Decimal("32.50"))
        self.assertEqual(result["tier2"], Decimal("325.00"))
        self.assertEqual(result["ssnit_employer"], Decimal("845.00"))
        self.assertEqual(result["paye"], Decimal("0.00"))
        self.assertEqual(result["total_deductions"], Decimal("357.50"))
        self.assertEqual(result["net_pay"], Decimal("6142.50"))


class PayeBandCrossingTests(TestCase):
    """Exercises the graduated PAYE calculation at both ends: a taxable
    income that never leaves the 0% first band, and one that crosses
    several bands and must reproduce a real, previously-fixed rounding
    bug (see docs/DESIGN.md and payroll.py's _calculate_paye docstring)."""

    def setUp(self):
        self.payroll_run = _make_payroll_run()

    def test_low_salary_stays_entirely_in_band_one_zero_tax(self):
        # basic 400: ssnit = 2.00, tier2 = 20.00, taxable_income =
        # 400 - 2 - 20 = 378.00, which never reaches band 1's own upper
        # bound (490.00) — the whole thing is taxed at band 1's 0% rate.
        employee = _make_employee("EMP-LOWSAL-001")
        _make_pay_config(employee, Decimal("400.00"))

        result = calculate_payslip(employee, self.payroll_run)

        self.assertEqual(result["ssnit"], Decimal("2.00"))
        self.assertEqual(result["tier2"], Decimal("20.00"))
        self.assertEqual(result["paye"], Decimal("0.00"))
        self.assertEqual(result["total_deductions"], Decimal("22.00"))
        self.assertEqual(result["net_pay"], Decimal("378.00"))

    def test_multi_band_paye_uses_per_band_rounding_not_sum_then_round(self):
        """Regression test for the real one-cent drift found in Session 3:
        summing every band's contribution at full precision and rounding
        the total ONCE gives the wrong answer (1,134.12) for a basic-6,500
        payslip; rounding EACH band's own contribution to 2dp before
        adding it to the running total — what _calculate_paye() actually
        does — gives the ERP-verified correct answer (1,134.13). This
        independently re-derives the "sum-then-round" figure directly
        from the seeded PAYEBand table (not by calling _calculate_paye or
        any other payroll.py internal) to prove the two approaches really
        do disagree on this input, not just assert a bare expected value."""
        employee = _make_employee("EMP-MULTIBAND-001")
        _make_pay_config(employee, Decimal("6500.00"))

        result = calculate_payslip(employee, self.payroll_run)
        self.assertEqual(result["paye"], Decimal("1134.13"))

        taxable_income = result["basic_salary"] - result["ssnit"] - result["tier2"]
        self.assertEqual(taxable_income, Decimal("6142.50"))

        bands = PAYEBand.objects.filter(effective_from=STATUTORY_EFFECTIVE_FROM).order_by("band_order")
        naive_total = Decimal("0")
        for band in bands:
            if taxable_income <= band.lower_bound:
                continue
            ceiling = band.upper_bound if band.upper_bound is not None else taxable_income
            amount_in_band = min(taxable_income, ceiling) - band.lower_bound
            if amount_in_band > 0:
                naive_total += amount_in_band * band.rate  # deliberately NOT rounded per band
        naive_sum_then_round = naive_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        self.assertEqual(naive_sum_then_round, Decimal("1134.12"))
        self.assertNotEqual(naive_sum_then_round, result["paye"])


class EffectiveDatingTests(TestCase):
    """calculate_payslip() must pick the EmployeePayConfig actually in
    force for the PayrollRun's own period, not just the latest row that
    exists — see _get_effective_pay_config()."""

    def setUp(self):
        # Both configs' windows are chosen to fall on/after
        # STATUTORY_EFFECTIVE_FROM (2025-01-01) deliberately — the seeded
        # StatutoryRate/PAYEBand rows (migration 0002) only cover dates
        # from then on, and EmployeePayConfig effective-dating is a
        # separate mechanism from statutory-data effective-dating; a
        # period has to be covered by BOTH to calculate at all.
        self.employee = _make_employee("EMP-RAISE-001")
        # An old, closed-out config...
        _make_pay_config(
            self.employee, Decimal("5000.00"),
            effective_from=date(2025, 1, 1), effective_to=date(2025, 6, 30),
        )
        # ...superseded by a current, open-ended one.
        _make_pay_config(
            self.employee, Decimal("6500.00"),
            effective_from=date(2025, 7, 1), effective_to=None,
        )

    def test_period_within_old_closed_config_picks_that_one(self):
        run = _make_payroll_run(month=3, year=2025)
        result = calculate_payslip(self.employee, run)
        self.assertEqual(result["basic_salary"], Decimal("5000.00"))

    def test_period_within_current_open_ended_config_picks_that_one(self):
        run = _make_payroll_run(month=9, year=2026)
        result = calculate_payslip(self.employee, run)
        self.assertEqual(result["basic_salary"], Decimal("6500.00"))

    def test_unapproved_config_is_never_picked_even_if_more_recent(self):
        # A pending raise that hasn't been approved yet must not silently
        # take effect — the approved 6,500 config should still win.
        _make_pay_config(
            self.employee, Decimal("7500.00"),
            effective_from=date(2026, 6, 1), effective_to=None,
            approval_status="pending",
        )
        run = _make_payroll_run(month=9, year=2026)
        result = calculate_payslip(self.employee, run)
        self.assertEqual(result["basic_salary"], Decimal("6500.00"))


class MissingConfigTests(TestCase):
    """No approved EmployeePayConfig for the requested period must raise
    PayrollConfigError, never silently produce a zero/wrong payslip — the
    original Session 2 design decision (see payroll.py's module docstring
    and PayrollConfigError's own docstring)."""

    def test_no_pay_config_at_all_raises(self):
        employee = _make_employee("EMP-NOCONFIG-001")
        run = _make_payroll_run()
        with self.assertRaises(PayrollConfigError):
            calculate_payslip(employee, run)

    def test_only_pending_pay_config_raises(self):
        employee = _make_employee("EMP-PENDINGONLY-001")
        _make_pay_config(employee, Decimal("6500.00"), approval_status="pending")
        run = _make_payroll_run()
        with self.assertRaises(PayrollConfigError):
            calculate_payslip(employee, run)

    def test_config_effective_after_the_run_period_raises(self):
        # A config that only starts next year can't cover a run for now.
        employee = _make_employee("EMP-FUTURECONFIG-001")
        _make_pay_config(employee, Decimal("6500.00"), effective_from=date(2027, 1, 1))
        run = _make_payroll_run(month=9, year=2026)
        with self.assertRaises(PayrollConfigError):
            calculate_payslip(employee, run)

    def test_config_that_already_expired_raises(self):
        employee = _make_employee("EMP-EXPIREDCONFIG-001")
        _make_pay_config(
            employee, Decimal("6500.00"),
            effective_from=date(2020, 1, 1), effective_to=date(2020, 12, 31),
        )
        run = _make_payroll_run(month=9, year=2026)
        with self.assertRaises(PayrollConfigError):
            calculate_payslip(employee, run)


class StatutorySeedDataTests(TestCase):
    """Confirms migration 0002 actually loaded the real GRA 2024 PAYE
    bands and the real Act 766 statutory rates — not a placeholder set —
    since every test above implicitly depends on that being true. See
    migrations/0002_seed_statutory_data.py and docs/CONSTRAINTS.md's
    statutory-accuracy notes."""

    def test_seven_real_gra_bands_are_seeded(self):
        bands = list(
            PAYEBand.objects.filter(effective_from=STATUTORY_EFFECTIVE_FROM).order_by("band_order")
        )
        self.assertEqual(len(bands), 7)

        expected = [
            (1, Decimal("0.00"), Decimal("490.00"), Decimal("0.0000")),
            (2, Decimal("490.00"), Decimal("600.00"), Decimal("0.0500")),
            (3, Decimal("600.00"), Decimal("730.00"), Decimal("0.1000")),
            (4, Decimal("730.00"), Decimal("3896.67"), Decimal("0.1750")),
            (5, Decimal("3896.67"), Decimal("19896.67"), Decimal("0.2500")),
            (6, Decimal("19896.67"), Decimal("50000.00"), Decimal("0.3000")),
            (7, Decimal("50000.00"), None, Decimal("0.3500")),
        ]
        for band, (order, lower, upper, rate) in zip(bands, expected):
            self.assertEqual(band.band_order, order)
            self.assertEqual(band.lower_bound, lower)
            self.assertEqual(band.upper_bound, upper)
            self.assertEqual(band.rate, rate)

    def test_real_statutory_rates_are_seeded(self):
        rate = StatutoryRate.objects.get(effective_from=STATUTORY_EFFECTIVE_FROM)
        self.assertEqual(rate.ssnit_employee_pct, Decimal("0.0050"))
        self.assertEqual(rate.ssnit_employer_pct, Decimal("0.1300"))
        self.assertEqual(rate.tier2_employee_pct, Decimal("0.0500"))
