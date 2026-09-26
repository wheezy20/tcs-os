"""Finance module — Merge Phase 2, Session 1. Ports the ERP's chart of
accounts and double-entry bookkeeping models (Account, JournalEntry,
JournalLine) plus campus-scoped operating expenses (ExpenseCategory,
Expense). See docs/DESIGN.md for the cross-module conventions this
follows (effective-dated vs. current-state fields, idempotent seed data,
DB-level constraints as the real guarantee with clean() as a friendly
admin-form error on top).

Expense.id and JournalEntry.id are real text primary keys
("EXP-YYYY-MM-####" / "JE-YYYY-MM-####"), generated via the shared
tcs_os.reference_counter.ReferenceCounter — see that module's docstring
for the full list of key formats now in use across admissions and
finance. Year/month in the ID reflect the record's own accounting date
(Expense.date / JournalEntry.entry_date), not the moment it was saved —
a backdated expense entered late still numbers into the period it
actually belongs to.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from modules.admissions.models import Campus
from tcs_os.reference_counter import ReferenceCounter


class Account(models.Model):
    """One row in the chart of accounts. `normal_balance` mirrors the
    ERP's Postgres generated column (debit for Assets/Expenses, credit
    otherwise) — Django has no portable generated-column field, so this
    is a plain @property, derived from `category` every time it's read
    rather than stored, so it can never drift out of sync with a
    category change."""

    CATEGORY_CHOICES = [
        ("Assets", "Assets"),
        ("Liabilities", "Liabilities"),
        ("Equity", "Equity"),
        ("Revenue", "Revenue"),
        ("Expenses", "Expenses"),
    ]
    DEBIT_NORMAL_CATEGORIES = {"Assets", "Expenses"}

    code = models.CharField(
        max_length=6, unique=True,
        validators=[RegexValidator(r"^[0-9]{3,6}$", "Account code must be 3-6 digits.")],
        help_text="Matches the ERP's own constraint on account codes.",
    )
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    subtype = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="accounts_created",
        help_text="Null for system-seeded chart-of-accounts rows (the real chart-of-accounts seed "
        "migration has no real user to attribute them to) — mirrors the ERP's own resolution of "
        "the identical problem in its 20260819090000 migration. Set for any account created for "
        "real afterward.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    @property
    def normal_balance(self):
        return "debit" if self.category in self.DEBIT_NORMAL_CATEGORIES else "credit"

    def __str__(self):
        return f"{self.code} — {self.name}"


class ExpenseCategory(models.Model):
    """A campus-scoped expense category — mirrors the ERP's
    branch_id-scoped uniqueness exactly, just with Campus (this
    project's own concept, cross-referenced from admissions) instead of
    the ERP's Branch. PROTECT on campus: a category shouldn't vanish
    (and silently orphan every Expense that references it) if its Campus
    record does."""

    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="expense_categories")
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField(default=0, help_text="Manual display ordering within a campus.")

    class Meta:
        ordering = ["campus", "position", "name"]
        unique_together = ("campus", "name")
        verbose_name_plural = "expense categories"

    def __str__(self):
        return f"{self.name} ({self.campus})"


class Expense(models.Model):
    """One recorded operating expense. `id` is a real text primary key
    ("EXP-YYYY-MM-####"), assigned once in save() on first creation only
    — never regenerated on a later update, so an expense's own reference
    number is permanent even if its date/category/etc. are corrected
    afterward."""

    METHOD_CHOICES = [
        ("Cash", "Cash"),
        ("Mobile Money", "Mobile Money"),
        ("Bank", "Bank"),
    ]

    id = models.CharField(primary_key=True, max_length=20, editable=False)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="expenses")
    date = models.DateField(help_text="The expense's own accounting date — also what numbers its id.")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    description = models.CharField(max_length=255)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))],
    )
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    reference = models.CharField(max_length=100, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expenses_recorded",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    receipt_path = models.CharField(
        max_length=500, blank=True,
        help_text="Path within a Supabase Storage bucket — not a URL. Same convention as every "
        "other file reference in this project (see modules.admissions.models.Document.file_path, "
        "modules.hr.models.EmployeeGeneratedDocument.storage_path).",
    )

    class Meta:
        ordering = ["-date", "-recorded_at"]

    def save(self, *args, **kwargs):
        if not self.id:
            key = f"EXP-{self.date.year}-{self.date.month:02d}"
            seq = ReferenceCounter.next_for(key)
            self.id = f"EXP-{self.date.year}-{self.date.month:02d}-{seq:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.id} — {self.description}"


class JournalEntry(models.Model):
    """One double-entry bookkeeping entry — a header row; its actual
    debit/credit lines live on JournalLine. `id` is a real text primary
    key ("JE-YYYY-MM-####"), assigned once in save() on first creation,
    same convention as Expense.id. `reverses_entry` mirrors the ERP's
    reverses_entry_id — a self-FK for reversal traceability (an entry
    posted in error is reversed by a new entry, never edited/deleted in
    place; this field records which original entry a reversal undoes)."""

    id = models.CharField(primary_key=True, max_length=20, editable=False)
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="journal_entries")
    entry_date = models.DateField(help_text="The entry's own accounting date — also what numbers its id.")
    description = models.CharField(max_length=255)
    reference = models.CharField(max_length=100, blank=True)
    reverses_entry = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by",
        help_text="Set on a reversal entry, pointing at the original entry it reverses.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="journal_entries_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-created_at"]
        verbose_name_plural = "journal entries"

    def save(self, *args, **kwargs):
        if not self.id:
            key = f"JE-{self.entry_date.year}-{self.entry_date.month:02d}"
            seq = ReferenceCounter.next_for(key)
            self.id = f"JE-{self.entry_date.year}-{self.entry_date.month:02d}-{seq:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.id} — {self.description}"


class JournalLine(models.Model):
    """One debit-or-credit line on a JournalEntry. Three integrity rules
    — debit >= 0, credit >= 0, never both debit and credit positive on
    the same line, never both zero — are the ERP's actual double-entry
    bookkeeping guarantees. Enforced at TWO layers deliberately: the DB
    CheckConstraints below are the real guarantee (hold even against a
    bulk load, a shell session, or any future code path that skips
    clean()); clean() gives a friendly admin-form error on top, but is
    not itself load-bearing. Do not relax either layer."""

    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveIntegerField(default=0, help_text="Manual display ordering within an entry.")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    debit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["entry", "position"]
        constraints = [
            models.CheckConstraint(condition=models.Q(debit__gte=0), name="journalline_debit_gte_0"),
            models.CheckConstraint(condition=models.Q(credit__gte=0), name="journalline_credit_gte_0"),
            models.CheckConstraint(
                condition=~(models.Q(debit__gt=0) & models.Q(credit__gt=0)),
                name="journalline_not_both_positive",
            ),
            models.CheckConstraint(
                condition=~(models.Q(debit=0) & models.Q(credit=0)),
                name="journalline_not_both_zero",
            ),
        ]

    def clean(self):
        errors = {}
        if self.debit < 0:
            errors["debit"] = "Must be zero or positive."
        if self.credit < 0:
            errors["credit"] = "Must be zero or positive."
        if errors:
            raise ValidationError(errors)
        if self.debit > 0 and self.credit > 0:
            raise ValidationError("A journal line cannot have both a debit and a credit amount.")
        if self.debit == 0 and self.credit == 0:
            raise ValidationError("A journal line must have either a debit or a credit amount.")

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit > 0 else f"Cr {self.credit}"
        return f"{self.entry_id} — {self.account.code} {side}"
