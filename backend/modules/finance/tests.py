"""Coverage for the finance module's chart of accounts and double-entry
bookkeeping models (Merge Phase 2, Session 1), posting a PayrollRun to
the ledger (Session 2), plus a confirmation that the ReferenceCounter
move (admissions -> tcs_os.reference_counter) is a pure relocation —
see modules/admissions/tests.py for admissions' own unchanged test
count, and tcs_os/reference_counter.py's docstring for the real key
formats these tests exercise.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from modules.admissions.models import Campus
from tcs_os.reference_counter import ReferenceCounter

from .models import Account, Expense, ExpenseCategory, ExpenseCategoryAccount, JournalEntry, JournalLine
from .posting import PostingError, post_expense, post_payroll_run


def _make_user(username="accountant"):
    return User.objects.create_user(username=username, password="pw12345")


def _make_account(code, category, name="Test Account", subtype="Test", user=None):
    return Account.objects.create(
        code=code, name=name, category=category, subtype=subtype, created_by=user or _make_user("acct-owner"),
    )


class AccountTests(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_normal_balance_debit_for_assets_and_expenses(self):
        assets = _make_account("9010", "Assets", user=self.user)
        expenses = _make_account("5010", "Expenses", user=self.user)
        self.assertEqual(assets.normal_balance, "debit")
        self.assertEqual(expenses.normal_balance, "debit")

    def test_normal_balance_credit_for_liabilities_equity_revenue(self):
        liabilities = _make_account("2010", "Liabilities", user=self.user)
        equity = _make_account("3010", "Equity", user=self.user)
        revenue = _make_account("4010", "Revenue", user=self.user)
        self.assertEqual(liabilities.normal_balance, "credit")
        self.assertEqual(equity.normal_balance, "credit")
        self.assertEqual(revenue.normal_balance, "credit")

    def test_normal_balance_is_derived_not_stored_and_never_drifts(self):
        account = _make_account("9020", "Assets", user=self.user)
        self.assertEqual(account.normal_balance, "debit")
        account.category = "Liabilities"
        # Not saved yet — but normal_balance reads the in-memory category
        # live, so it already reflects the pending change; the point is
        # there's no separate stored field that could disagree with it.
        self.assertEqual(account.normal_balance, "credit")

    def test_code_must_match_three_to_six_digits(self):
        for bad_code in ("12", "1234567", "12a4", ""):
            account = Account(code=bad_code, name="Bad", category="Assets", subtype="x", created_by=self.user)
            with self.assertRaises(ValidationError):
                account.full_clean()

    def test_code_must_be_unique(self):
        _make_account("1030", "Assets", user=self.user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _make_account("1030", "Liabilities", user=self.user)


class ExpenseCategoryTests(TestCase):
    def setUp(self):
        self.campus = Campus.objects.create(name="Test Campus")

    def test_unique_together_campus_and_name(self):
        ExpenseCategory.objects.create(campus=self.campus, name="Utilities")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExpenseCategory.objects.create(campus=self.campus, name="Utilities")

    def test_same_name_allowed_across_different_campuses(self):
        other_campus = Campus.objects.create(name="Other Campus")
        ExpenseCategory.objects.create(campus=self.campus, name="Utilities")
        # Should not raise — uniqueness is scoped per campus.
        ExpenseCategory.objects.create(campus=other_campus, name="Utilities")

    def test_campus_cannot_be_deleted_while_referenced(self):
        ExpenseCategory.objects.create(campus=self.campus, name="Utilities")
        with self.assertRaises(Exception):  # ProtectedError
            self.campus.delete()


class ExpenseTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.campus = Campus.objects.create(name="Test Campus")
        self.category = ExpenseCategory.objects.create(campus=self.campus, name="Utilities")

    def _make_expense(self, expense_date, description="Electricity", amount=Decimal("450.00")):
        return Expense.objects.create(
            campus=self.campus, date=expense_date, category=self.category, description=description,
            amount=amount, method="Bank", recorded_by=self.user,
        )

    def test_id_format_reflects_the_expense_own_date(self):
        expense = self._make_expense(date(2026, 9, 15))
        self.assertEqual(expense.id, "EXP-2026-09-0001")

    def test_repeated_creation_in_same_month_increments_sequence(self):
        first = self._make_expense(date(2026, 9, 15))
        second = self._make_expense(date(2026, 9, 20), description="Water")
        self.assertEqual(first.id, "EXP-2026-09-0001")
        self.assertEqual(second.id, "EXP-2026-09-0002")

    def test_id_is_never_regenerated_on_update(self):
        expense = self._make_expense(date(2026, 9, 15))
        original_id = expense.id
        expense.description = "Electricity (corrected)"
        expense.save()
        self.assertEqual(expense.id, original_id)

    def test_amount_must_be_positive(self):
        expense = Expense(
            campus=self.campus, date=date(2026, 9, 1), category=self.category, description="x",
            amount=Decimal("0.00"), method="Cash", recorded_by=self.user,
        )
        with self.assertRaises(ValidationError):
            expense.full_clean()

    def test_category_cannot_be_deleted_while_referenced(self):
        self._make_expense(date(2026, 9, 15))
        with self.assertRaises(Exception):  # ProtectedError
            self.category.delete()


class JournalEntryTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.campus = Campus.objects.create(name="Test Campus")

    def test_id_format_reflects_the_entry_own_date(self):
        entry = JournalEntry.objects.create(
            campus=self.campus, entry_date=date(2026, 9, 1), description="Tuition received", created_by=self.user,
        )
        self.assertEqual(entry.id, "JE-2026-09-0001")

    def test_id_is_never_regenerated_on_update(self):
        entry = JournalEntry.objects.create(
            campus=self.campus, entry_date=date(2026, 9, 1), description="Tuition received", created_by=self.user,
        )
        original_id = entry.id
        entry.description = "Tuition received (corrected)"
        entry.save()
        self.assertEqual(entry.id, original_id)

    def test_reverses_entry_links_to_the_original(self):
        original = JournalEntry.objects.create(
            campus=self.campus, entry_date=date(2026, 9, 1), description="Original", created_by=self.user,
        )
        reversal = JournalEntry.objects.create(
            campus=self.campus, entry_date=date(2026, 9, 2), description="Reversal",
            created_by=self.user, reverses_entry=original,
        )
        self.assertEqual(reversal.reverses_entry_id, original.id)
        self.assertIn(reversal, original.reversed_by.all())

    def test_expense_and_journal_entry_ids_share_the_period_but_never_collide(self):
        """EXP- and JE- are separate ReferenceCounter keys even for the
        same year/month — confirms the shared counter doesn't conflate
        different callers' sequences."""
        category = ExpenseCategory.objects.create(campus=self.campus, name="Utilities")
        expense = Expense.objects.create(
            campus=self.campus, date=date(2026, 9, 1), category=category, description="x",
            amount=Decimal("10.00"), method="Cash", recorded_by=self.user,
        )
        entry = JournalEntry.objects.create(
            campus=self.campus, entry_date=date(2026, 9, 1), description="x", created_by=self.user,
        )
        self.assertEqual(expense.id, "EXP-2026-09-0001")
        self.assertEqual(entry.id, "JE-2026-09-0001")  # not "0002" — independent sequences


class JournalLineTests(TestCase):
    """The three double-entry integrity rules — debit >= 0, credit >= 0,
    never both positive, never both zero — verified at BOTH layers:
    clean() (a friendly error) and the DB CheckConstraints (the real
    guarantee, confirmed here by bypassing clean() entirely)."""

    def setUp(self):
        self.user = _make_user()
        self.campus = Campus.objects.create(name="Test Campus")
        self.account = _make_account("9010", "Assets", user=self.user)
        self.entry = JournalEntry.objects.create(
            campus=self.campus, entry_date=date(2026, 9, 1), description="Test entry", created_by=self.user,
        )

    def test_valid_debit_only_line_saves(self):
        line = JournalLine(entry=self.entry, account=self.account, debit=Decimal("100.00"), credit=Decimal("0.00"))
        line.full_clean()
        line.save()
        self.assertEqual(line.debit, Decimal("100.00"))

    def test_valid_credit_only_line_saves(self):
        line = JournalLine(entry=self.entry, account=self.account, debit=Decimal("0.00"), credit=Decimal("100.00"))
        line.full_clean()
        line.save()
        self.assertEqual(line.credit, Decimal("100.00"))

    def test_clean_rejects_both_positive(self):
        line = JournalLine(entry=self.entry, account=self.account, debit=Decimal("50"), credit=Decimal("50"))
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_clean_rejects_both_zero(self):
        line = JournalLine(entry=self.entry, account=self.account, debit=Decimal("0"), credit=Decimal("0"))
        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_clean_rejects_negative_debit_or_credit(self):
        with self.assertRaises(ValidationError):
            JournalLine(entry=self.entry, account=self.account, debit=Decimal("-5"), credit=Decimal("0")).full_clean()
        with self.assertRaises(ValidationError):
            JournalLine(entry=self.entry, account=self.account, debit=Decimal("0"), credit=Decimal("-5")).full_clean()

    def test_db_rejects_both_positive_even_bypassing_clean(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalLine.objects.create(
                    entry=self.entry, account=self.account, debit=Decimal("50"), credit=Decimal("50"),
                )

    def test_db_rejects_both_zero_even_bypassing_clean(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalLine.objects.create(
                    entry=self.entry, account=self.account, debit=Decimal("0"), credit=Decimal("0"),
                )

    def test_db_rejects_negative_debit_even_bypassing_clean(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalLine.objects.create(
                    entry=self.entry, account=self.account, debit=Decimal("-5"), credit=Decimal("0"),
                )

    def test_db_rejects_negative_credit_even_bypassing_clean(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                JournalLine.objects.create(
                    entry=self.entry, account=self.account, debit=Decimal("0"), credit=Decimal("-5"),
                )

    def test_account_cannot_be_deleted_while_referenced_by_a_line(self):
        JournalLine.objects.create(entry=self.entry, account=self.account, debit=Decimal("100.00"), credit=Decimal("0.00"))
        with self.assertRaises(Exception):  # ProtectedError
            self.account.delete()


class ChartOfAccountsSeedDataTests(TestCase):
    """Confirms migration 0002 seeded the real, verified ERP chart of
    accounts — 46 rows (41 base chart + 5 payroll-related accounts
    added ahead of Merge Phase 2 Session 2) — not a placeholder set."""

    def test_46_accounts_seeded(self):
        self.assertEqual(Account.objects.count(), 46)

    def test_payroll_accounts_present_with_correct_categories(self):
        expected = {
            "2310": "Liabilities",
            "2320": "Liabilities",
            "2330": "Liabilities",
            "4910": "Revenue",
            "5145": "Expenses",
        }
        for code, category in expected.items():
            account = Account.objects.get(code=code)
            self.assertEqual(account.category, category)

    def test_5146_was_not_seeded(self):
        """5146 (Employer Tier 2 Contribution) doesn't exist anywhere in
        the ERP's own migrations, and was confirmed 2026-09-26 to be a
        byproduct of the reverted Tier 2 incident (docs/DESIGN.md) —
        should never be created. See migration 0002's own docstring."""
        self.assertFalse(Account.objects.filter(code="5146").exists())

    def test_seeded_rows_have_no_created_by(self):
        # Mirrors the ERP's own resolution of the identical problem —
        # system-seeded rows have no real user to attribute them to.
        self.assertIsNone(Account.objects.get(code="1000").created_by)


class ReferenceCounterRelocationTests(TestCase):
    """Confirms the ReferenceCounter move (admissions/models.py ->
    tcs_os/reference_counter.py) is genuinely a single shared model —
    same class, same table, same locking behavior — not two divergent
    copies. admissions' own tests.py separately confirms its own
    inquiry/application/student-ID call sites still pass unchanged."""

    def test_admissions_and_finance_import_the_identical_class(self):
        from modules.admissions.models import ReferenceCounter as AdmissionsReferenceCounter
        self.assertIs(AdmissionsReferenceCounter, ReferenceCounter)

    def test_app_label_stays_admissions_for_existing_migration_history(self):
        self.assertEqual(ReferenceCounter._meta.app_label, "admissions")

    def test_next_for_is_atomic_and_sequential_per_key(self):
        first = ReferenceCounter.next_for("TEST-KEY")
        second = ReferenceCounter.next_for("TEST-KEY")
        third = ReferenceCounter.next_for("TEST-KEY")
        self.assertEqual([first, second, third], [1, 2, 3])

    def test_different_keys_have_independent_sequences(self):
        self.assertEqual(ReferenceCounter.next_for("TEST-KEY-A"), 1)
        self.assertEqual(ReferenceCounter.next_for("TEST-KEY-B"), 1)
        self.assertEqual(ReferenceCounter.next_for("TEST-KEY-A"), 2)


class PostPayrollRunTests(TestCase):
    """Merge Phase 2, Session 2 — posts a PayrollRun's payslips to the
    ledger as ONE aggregated JournalEntry. A realistic multi-employee
    run: Emmanuel Ansah's real, independently-verified ERP ground-truth
    payslip (see modules.hr.tests.EmmanuelAnsahGroundTruthTests — basic
    6,500, all pays_* true, no overtime/allowances/fines/iou) generated
    via the actual calculate_payslip(), plus a second, hand-constructed
    payslip with non-zero fines/iou so those two optional lines are
    exercised too. Every expected aggregate/line figure below is
    independent, hand-added arithmetic on these two payslips' own
    fields — not derived by calling post_payroll_run() and trusting its
    own output."""

    def setUp(self):
        from modules.hr.models import Employee, EmployeePayConfig, PayrollRun, Payslip
        from modules.hr.payroll import calculate_payslip

        self.user = _make_user("accountant2")
        self.campus, _ = Campus.objects.get_or_create(name="Main")
        self.run = PayrollRun.objects.create(branch=self.campus, month=9, year=2026, status="draft")

        ansah = Employee.objects.create(
            employee_number="EMP-POST-ANSAH", first_name="Emmanuel", surname="Ansah",
            basic_salary=Decimal("0"), employment_status="active",
        )
        EmployeePayConfig.objects.create(
            employee=ansah, basic_salary=Decimal("6500.00"), pays_ssnit=True, pays_tier2=True, pays_paye=True,
            effective_from=date(2025, 1, 1), approval_status="approved",
        )
        Payslip.objects.create(payroll_run=self.run, employee=ansah, **calculate_payslip(ansah, self.run))

        mensah = Employee.objects.create(
            employee_number="EMP-POST-MENSAH", first_name="Kofi", surname="Mensah",
            basic_salary=Decimal("0"), employment_status="active",
        )
        # Hand-constructed, not via calculate_payslip — deliberately so
        # this test's expected aggregate doesn't depend on trusting the
        # same function it's meant to independently exercise fines/iou
        # against. basic 3000: ssnit 0.5%=15.00, tier2 5%=150.00,
        # ssnit_employer 13%=390.00 (all real statutory percentages,
        # just hand-applied here); paye/fines/iou are plain chosen
        # figures for this test.
        Payslip.objects.create(
            payroll_run=self.run, employee=mensah,
            basic_salary=Decimal("3000.00"), overtime_pay=Decimal("0"), total_allowances=Decimal("0"),
            gross_salary=Decimal("3000.00"), ssnit=Decimal("15.00"), tier2=Decimal("150.00"),
            paye=Decimal("200.00"), ssnit_employer=Decimal("390.00"), fines=Decimal("50.00"),
            iou=Decimal("100.00"), total_deductions=Decimal("515.00"), net_pay=Decimal("2485.00"),
        )

        self.run.status = "ready_for_review"
        self.run.save()

    def test_entry_balances_and_every_line_is_correct(self):
        entry = post_payroll_run(self.run, created_by=self.user)

        self.assertEqual(entry.campus, self.campus)
        self.assertEqual(entry.entry_date, date(2026, 9, 30))  # last calendar day of the run's month
        self.assertEqual(entry.description, "Payroll — September 2026")
        self.assertEqual(entry.created_by, self.user)

        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(set(lines), {"5140", "2300", "2310", "5145", "2320", "2330", "1350", "4910"})
        # 2310 (SSNIT Payable) carries BOTH the employee and employer
        # portions as two separate lines on the same account — confirm
        # both are present, not collapsed into one.
        ssnit_payable_lines = [line for line in entry.lines.all() if line.account.code == "2310"]
        self.assertEqual(len(ssnit_payable_lines), 2)

        self.assertEqual(lines["5140"].debit, Decimal("9500.00"))   # gross: 6500 + 3000
        self.assertEqual(lines["5140"].credit, Decimal("0.00"))
        self.assertEqual(lines["2300"].credit, Decimal("7493.37"))  # net: 5008.37 + 2485.00
        self.assertEqual(lines["5145"].debit, Decimal("1235.00"))   # employer ssnit: 845 + 390
        self.assertEqual(lines["2320"].credit, Decimal("475.00"))   # tier2: 325 + 150
        self.assertEqual(lines["2330"].credit, Decimal("1334.13"))  # paye: 1134.13 + 200
        self.assertEqual(lines["1350"].credit, Decimal("100.00"))   # iou: 0 + 100
        self.assertEqual(lines["4910"].credit, Decimal("50.00"))    # fines: 0 + 50
        ssnit_amounts = sorted(line.credit for line in ssnit_payable_lines)
        self.assertEqual(ssnit_amounts, [Decimal("47.50"), Decimal("1235.00")])  # employee 32.50+15, employer 845+390

        total_debit = sum(line.debit for line in entry.lines.all())
        total_credit = sum(line.credit for line in entry.lines.all())
        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal("10735.00"))

    def test_run_status_flips_to_posted(self):
        post_payroll_run(self.run, created_by=self.user)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "posted")

    def test_zero_value_lines_are_never_created(self):
        """Both test payslips have pays_paye/ssnit/tier2 all true and
        nonzero, so this run's own entry has no zero-value line to
        check — a dedicated single-employee, no-fines/no-iou run
        confirms iou/fines really are omitted, not just present-with-
        zero (which would violate JournalLine's own CHECK constraint
        anyway, but this confirms it's skipped deliberately, not by
        accident relying on that constraint to fail loudly)."""
        from modules.hr.models import Employee, EmployeePayConfig, PayrollRun, Payslip
        from modules.hr.payroll import calculate_payslip

        run = PayrollRun.objects.create(branch=self.campus, month=10, year=2026, status="draft")
        employee = Employee.objects.create(
            employee_number="EMP-POST-NOFINES", first_name="Ama", surname="Boateng",
            basic_salary=Decimal("0"), employment_status="active",
        )
        EmployeePayConfig.objects.create(
            employee=employee, basic_salary=Decimal("6500.00"), pays_ssnit=True, pays_tier2=True, pays_paye=True,
            effective_from=date(2025, 1, 1), approval_status="approved",
        )
        Payslip.objects.create(payroll_run=run, employee=employee, **calculate_payslip(employee, run))
        run.status = "ready_for_review"
        run.save()

        entry = post_payroll_run(run, created_by=self.user)
        codes = {line.account.code for line in entry.lines.all()}
        self.assertNotIn("1350", codes)  # no iou
        self.assertNotIn("4910", codes)  # no fines

    def test_cannot_post_a_draft_run(self):
        self.run.status = "draft"
        self.run.save()
        with self.assertRaises(PostingError):
            post_payroll_run(self.run, created_by=self.user)

    def test_cannot_post_a_run_with_no_payslips(self):
        empty_run_campus, _ = Campus.objects.get_or_create(name="Annex")
        from modules.hr.models import PayrollRun as HrPayrollRun
        empty_run = HrPayrollRun.objects.create(
            branch=empty_run_campus, month=11, year=2026, status="ready_for_review",
        )
        with self.assertRaises(PostingError):
            post_payroll_run(empty_run, created_by=self.user)

    def test_second_post_attempt_is_rejected_not_a_duplicate_entry(self):
        post_payroll_run(self.run, created_by=self.user)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "posted")
        self.assertEqual(JournalEntry.objects.filter(campus=self.campus).count(), 1)

        with self.assertRaises(PostingError):
            post_payroll_run(self.run, created_by=self.user)
        self.assertEqual(JournalEntry.objects.filter(campus=self.campus).count(), 1)  # still just one


# --- Session 3: expense auto-posting + accounting views ---



class PostExpenseTests(TestCase):
    """Confirms post_expense() for each of the three payment methods,
    an unmapped category raising a clear error rather than posting
    something wrong, and idempotency (never a duplicate entry)."""

    def setUp(self):
        self.user = _make_user("accountant3")
        self.campus, _ = Campus.objects.get_or_create(name="Main")
        self.category = ExpenseCategory.objects.get(campus=self.campus, name="Transport")  # seeded by 0005, -> 5120

    def _make_expense(self, method, amount="150.00"):
        return Expense.objects.create(
            campus=self.campus, date=date(2026, 9, 15), category=self.category, description="Fuel",
            amount=Decimal(amount), method=method, recorded_by=self.user,
        )

    def test_cash_posts_to_cash_on_hand(self):
        expense = self._make_expense("Cash")
        entry = post_expense(expense, created_by=self.user)
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(set(lines), {"5120", "1000"})
        self.assertEqual(lines["5120"].debit, Decimal("150.00"))
        self.assertEqual(lines["1000"].credit, Decimal("150.00"))

    def test_bank_posts_to_cash_in_bank(self):
        expense = self._make_expense("Bank")
        entry = post_expense(expense, created_by=self.user)
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(set(lines), {"5120", "1010"})
        self.assertEqual(lines["1010"].credit, Decimal("150.00"))

    def test_mobile_money_posts_to_mobile_money_float(self):
        expense = self._make_expense("Mobile Money")
        entry = post_expense(expense, created_by=self.user)
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(set(lines), {"5120", "1020"})
        self.assertEqual(lines["1020"].credit, Decimal("150.00"))

    def test_entry_balances_and_fields_are_correct(self):
        expense = self._make_expense("Cash", amount="250.00")
        entry = post_expense(expense, created_by=self.user)
        self.assertEqual(entry.campus, self.campus)
        self.assertEqual(entry.entry_date, expense.date)
        self.assertEqual(entry.description, f"Expense — {expense.id}: Fuel")
        self.assertEqual(entry.expense, expense)
        total_debit = sum(line.debit for line in entry.lines.all())
        total_credit = sum(line.credit for line in entry.lines.all())
        self.assertEqual(total_debit, total_credit)
        self.assertEqual(total_debit, Decimal("250.00"))

    def test_unmapped_category_raises_clear_error_not_a_wrong_post(self):
        unmapped = ExpenseCategory.objects.create(campus=self.campus, name="Brand New Category")
        expense = Expense.objects.create(
            campus=self.campus, date=date(2026, 9, 15), category=unmapped, description="x",
            amount=Decimal("10.00"), method="Cash", recorded_by=self.user,
        )
        with self.assertRaises(PostingError):
            post_expense(expense, created_by=self.user)
        self.assertFalse(JournalEntry.objects.filter(expense=expense).exists())

    def test_unrecognized_payment_method_raises(self):
        # Bypasses model-level choices validation deliberately (direct
        # field assignment, no full_clean()) to confirm post_expense()
        # itself guards this, not just relying on the choices field.
        expense = self._make_expense("Cash")
        expense.method = "Barter"
        expense.save()
        with self.assertRaises(PostingError):
            post_expense(expense, created_by=self.user)

    def test_posting_twice_never_duplicates_the_entry(self):
        expense = self._make_expense("Cash")
        first = post_expense(expense, created_by=self.user)
        second = post_expense(expense, created_by=self.user)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(JournalEntry.objects.filter(expense=expense).count(), 1)

    def test_concurrent_post_race_resolves_to_one_entry_via_db_constraint(self):
        """Simulates the race the pre-check alone can't close: two calls
        both pass the "no existing entry" check before either commits.
        The OneToOneField's DB-level uniqueness is the real guarantee —
        confirmed here by forcing the pre-check to report "not yet
        posted" a second time, so the second create() actually hits the
        constraint rather than being pre-empted by the check."""
        expense = self._make_expense("Cash")
        first = post_expense(expense, created_by=self.user)

        with mock.patch(
            "modules.finance.models.JournalEntry.objects.filter",
            return_value=JournalEntry.objects.none(),
        ):
            second = post_expense(expense, created_by=self.user)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(JournalEntry.objects.filter(expense=expense).count(), 1)


class ExpenseCategoryAccountSeedDataTests(TestCase):
    """Confirms migration 0005 seeded the real ERP category/account
    mapping for both campuses — not a placeholder set."""

    def test_all_nine_categories_seeded_for_both_campuses(self):
        names = set(ExpenseCategory.objects.values_list("name", flat=True))
        self.assertEqual(
            names,
            {
                "Transport", "Fuel", "Rent", "Utilities", "Casual labour",
                "Repairs & maintenance", "Supplies", "Staff advances", "Miscellaneous",
            },
        )
        self.assertEqual(ExpenseCategory.objects.count(), 18)  # 9 x 2 campuses

    def test_mapping_matches_the_real_erp_data(self):
        expected = {
            "Transport": "5120", "Fuel": "5130", "Rent": "5100", "Utilities": "5110",
            "Casual labour": "5150", "Repairs & maintenance": "5160", "Supplies": "5170",
            "Staff advances": "1350", "Miscellaneous": "5900",
        }
        for name, code in expected.items():
            category = ExpenseCategory.objects.filter(name=name).first()
            self.assertEqual(category.account_mapping.account.code, code)

    def test_str_does_not_raise(self):
        """Regression test: a code-reviewer pass caught a duplicate
        __str__ definition on ExpenseCategoryAccount — the second
        (accidental copy-paste of ExpenseCategory's own __str__)
        silently overrode the first and referenced self.name/self.campus,
        neither of which exists on this model, so str() raised
        AttributeError. Nothing called str() on this model anywhere in
        the test suite, so this went uncaught until manual review."""
        mapping = ExpenseCategoryAccount.objects.first()
        self.assertIn("→", str(mapping))


class AccountingPermissionSeedDataTests(TestCase):
    def test_administration_gains_both_new_permissions(self):
        group = Group.objects.get(name="Administration")
        codenames = set(
            group.permissions.filter(content_type__app_label="finance").values_list("codename", flat=True)
        )
        self.assertEqual(codenames, {"can_manage_accounts", "can_record_expenses"})


class AccountViewTests(TestCase):
    def setUp(self):
        self.user = _make_user("accountsmanager")
        self.user.groups.add(Group.objects.get(name="Administration"))
        self.nobody = _make_user("plain")
        self.client.login(username="accountsmanager", password="pw12345")

    def test_list_requires_permission(self):
        self.client.logout()
        self.client.login(username="plain", password="pw12345")
        response = self.client.get("/finance/accounts/")
        self.assertEqual(response.status_code, 403)

    def test_create_account(self):
        response = self.client.post(
            "/finance/accounts/create/",
            {"code": "9500", "name": "Test Suspense", "category": "Assets", "subtype": "Current Asset", "description": ""},
        )
        self.assertRedirects(response, "/finance/accounts/")
        account = Account.objects.get(code="9500")
        self.assertEqual(account.created_by, self.user)

    def test_duplicate_code_rejected(self):
        Account.objects.create(code="9501", name="X", category="Assets", subtype="Y", created_by=self.user)
        response = self.client.post(
            "/finance/accounts/create/",
            {"code": "9501", "name": "Dup", "category": "Assets", "subtype": "Z", "description": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("already exists", response.context["errors"]["code"])

    def test_edit_deactivates_rather_than_deletes(self):
        account = Account.objects.create(code="9502", name="X", category="Assets", subtype="Y", created_by=self.user)
        response = self.client.post(
            f"/finance/accounts/{account.pk}/edit/",
            {"code": "9502", "name": "X", "category": "Assets", "subtype": "Y", "description": ""},  # is_active omitted
        )
        self.assertRedirects(response, "/finance/accounts/")
        account.refresh_from_db()
        self.assertFalse(account.is_active)
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())  # still exists — never deleted


class ExpenseViewTests(TestCase):
    def setUp(self):
        self.user = _make_user("recorder")
        self.user.groups.add(Group.objects.get(name="Administration"))
        self.campus, _ = Campus.objects.get_or_create(name="Main")
        self.category = ExpenseCategory.objects.get(campus=self.campus, name="Transport")
        self.client.login(username="recorder", password="pw12345")

    def test_create_requires_permission(self):
        nobody = _make_user("plain2")
        self.client.logout()
        self.client.login(username="plain2", password="pw12345")
        response = self.client.get("/finance/expenses/create/")
        self.assertEqual(response.status_code, 403)

    def test_successful_expense_creation_shows_the_posted_entry(self):
        response = self.client.post(
            "/finance/expenses/create/",
            {
                "campus": self.campus.pk, "category": self.category.pk, "date": "2026-09-15",
                "description": "Fuel", "amount": "150.00", "method": "Cash", "reference": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("entry", response.context)
        self.assertContains(response, "Posted to the ledger")

    def test_expense_list_filters_by_campus(self):
        Expense.objects.create(
            campus=self.campus, date=date(2026, 9, 1), category=self.category, description="A",
            amount=Decimal("10.00"), method="Cash", recorded_by=self.user,
        )
        other_campus, _ = Campus.objects.get_or_create(name="Annex")
        other_category = ExpenseCategory.objects.get(campus=other_campus, name="Transport")
        Expense.objects.create(
            campus=other_campus, date=date(2026, 9, 1), category=other_category, description="B",
            amount=Decimal("20.00"), method="Cash", recorded_by=self.user,
        )
        response = self.client.get(f"/finance/expenses/?campus={self.campus.pk}")
        self.assertEqual(len(response.context["expenses"]), 1)
        self.assertEqual(response.context["expenses"][0].description, "A")

    def test_mismatched_campus_and_category_is_rejected(self):
        """Regression test: a code-reviewer pass flagged that campus and
        category are independent form fields with no server-side check
        that they agree — confirms this is now caught explicitly rather
        than silently creating an Expense whose campus disagrees with
        its own category's campus."""
        other_campus, _ = Campus.objects.get_or_create(name="Annex")
        other_category = ExpenseCategory.objects.get(campus=other_campus, name="Transport")
        response = self.client.post(
            "/finance/expenses/create/",
            {
                "campus": self.campus.pk, "category": other_category.pk, "date": "2026-09-15",
                "description": "Fuel", "amount": "150.00", "method": "Cash", "reference": "",
            },
        )
        self.assertEqual(response.status_code, 200)  # re-rendered form, not created
        self.assertIn("different campus", response.context["errors"]["category"])
        self.assertFalse(Expense.objects.filter(description="Fuel").exists())


class ExpenseReceiptUploadURLViewTests(TestCase):
    """Regression tests: a code-reviewer pass flagged that this endpoint
    only checked for a non-empty filename — no extension or size check,
    unlike admissions' UploadURLRequestSerializer (the pattern this
    endpoint claims to mirror). Now validates both, matching that
    precedent exactly."""

    def setUp(self):
        self.user = _make_user("recorder2")
        self.user.groups.add(Group.objects.get(name="Administration"))
        self.client.login(username="recorder2", password="pw12345")

    def _post(self, filename, file_size=1000):
        import json as json_module
        return self.client.post(
            "/finance/expenses/receipt-upload-url/",
            data=json_module.dumps({"filename": filename, "file_size": file_size}),
            content_type="application/json",
        )

    def test_disallowed_extension_is_rejected(self):
        response = self._post("receipt.exe")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported file type", response.json()["detail"])

    def test_oversized_file_is_rejected(self):
        from django.conf import settings
        too_big = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1
        response = self._post("receipt.pdf", file_size=too_big)
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.json()["detail"])

    def test_allowed_extension_and_size_pass_validation(self):
        with mock.patch(
            "modules.finance.storage.create_upload_target",
            return_value=("some/path.pdf", "https://example.supabase.co/upload/signed"),
        ):
            response = self._post("receipt.pdf", file_size=1000)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["storage_path"], "some/path.pdf")


class JournalEntryViewTests(TestCase):
    def setUp(self):
        self.user = _make_user("viewer")
        self.user.groups.add(Group.objects.get(name="Administration"))
        self.campus, _ = Campus.objects.get_or_create(name="Main")
        self.category = ExpenseCategory.objects.get(campus=self.campus, name="Transport")
        self.client.login(username="viewer", password="pw12345")

    def test_list_and_detail_render(self):
        expense = Expense.objects.create(
            campus=self.campus, date=date(2026, 9, 1), category=self.category, description="A",
            amount=Decimal("10.00"), method="Cash", recorded_by=self.user,
        )
        entry = post_expense(expense, created_by=self.user)

        response = self.client.get("/finance/journal-entries/")
        self.assertEqual(response.status_code, 200)

        response = self.client.get(f"/finance/journal-entries/{entry.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["lines"]), 2)
