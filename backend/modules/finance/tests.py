"""Coverage for the finance module's chart of accounts and double-entry
bookkeeping models (Merge Phase 2, Session 1), plus a confirmation that
the ReferenceCounter move (admissions -> tcs_os.reference_counter) is a
pure relocation — see modules/admissions/tests.py for admissions' own
unchanged test count, and tcs_os/reference_counter.py's docstring for
the real key formats these tests exercise.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from modules.admissions.models import Campus
from tcs_os.reference_counter import ReferenceCounter

from .models import Account, Expense, ExpenseCategory, JournalEntry, JournalLine


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
