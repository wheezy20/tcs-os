# Seeds ExpenseCategory rows and their ExpenseCategoryAccount mapping —
# both real ERP data (tcs-erp/supabase/migrations/20260819090000_seed_gap_
# accounts_expense_categories.sql's expense_categories/expense_category_
# accounts inserts), not invented, same discipline as finance's 0002 chart-
# of-accounts seed.
#
# One real difference from the ERP source, flagged rather than silently
# copied: the ERP was single-branch when this data was written (it
# resolves "the one branch that exists" dynamically, see that migration's
# own `do $$ ... select id into v_branch_id from branches ... limit 1`
# block) — TCS OS already has two seeded Campus rows (Main, Annex; see
# admissions/migrations/0009_seed_campuses.py), so this migration seeds
# the same 9 categories for BOTH campuses rather than picking just one,
# since ExpenseCategory is genuinely campus-scoped here in a way the ERP's
# data never had to be.
#
# get_or_create() keyed on (campus, name) — ExpenseCategory's own
# unique_together — and on category — ExpenseCategoryAccount's own
# OneToOneField uniqueness.

from django.db import migrations

# (category name, mapped account code)
CATEGORIES_WITH_ACCOUNTS = [
    ("Transport", "5120"),
    ("Fuel", "5130"),
    ("Rent", "5100"),
    ("Utilities", "5110"),
    ("Casual labour", "5150"),
    ("Repairs & maintenance", "5160"),
    ("Supplies", "5170"),
    ("Staff advances", "1350"),
    ("Miscellaneous", "5900"),
]


def seed_expense_categories(apps, schema_editor):
    Campus = apps.get_model("admissions", "Campus")
    Account = apps.get_model("finance", "Account")
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")
    ExpenseCategoryAccount = apps.get_model("finance", "ExpenseCategoryAccount")

    for position, (name, account_code) in enumerate(CATEGORIES_WITH_ACCOUNTS):
        try:
            account = Account.objects.get(code=account_code)
        except Account.DoesNotExist:
            # Should never happen against this project's own migration
            # history (0002 seeds every code referenced here) — but if it
            # ever does, print loudly rather than silently under-seeding.
            # "Never silently no-op" is this project's own stated
            # discipline (see posting.py's PostingError docstring) — a
            # migration is no exception just because it can't raise and
            # roll back the same way application code can.
            print(
                f"  WARNING: no Account with code {account_code!r} for category "
                f"{name!r} — skipping this category's ExpenseCategoryAccount mapping."
            )
            continue

        for campus in Campus.objects.all():
            category, _ = ExpenseCategory.objects.get_or_create(
                campus=campus, name=name, defaults={"position": position},
            )
            ExpenseCategoryAccount.objects.get_or_create(
                category=category, defaults={"account": account},
            )


def remove_expense_categories(apps, schema_editor):
    ExpenseCategory = apps.get_model("finance", "ExpenseCategory")
    names = [name for name, _ in CATEGORIES_WITH_ACCOUNTS]
    # Cascades to each ExpenseCategoryAccount row too (on_delete=CASCADE).
    ExpenseCategory.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0004_accounting_permissions"),
        ("admissions", "0009_seed_campuses"),
    ]

    operations = [
        migrations.RunPython(seed_expense_categories, remove_expense_categories),
    ]
