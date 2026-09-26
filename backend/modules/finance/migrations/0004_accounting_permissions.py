# Session 3 — accounting permissions. Follows hr's 0004_payroll_permissions
# pattern exactly (itself following admissions' 0017), with the same
# .add()-not-.set() discipline on Administration.
#
# Two custom permissions: can_manage_accounts (on Account, added by 0003)
# and can_record_expenses (on Expense, added by 0003). Both granted to the
# existing "Administration" group only — no separate "Accountant" group
# this session, same reasoning as hr Session 6's self-approval-boundary
# decision (docs/CONSTRAINTS.md's "Payroll approval" section): don't build
# role separation nobody can use yet. Revisit once a real Accountant-role
# need exists.
#
# .add(), NOT .set() — Administration's admissions AND hr permissions
# (granted by their own migrations) are owned by those migrations, not
# this one; .set() here would silently wipe them. Administration is
# looked up with .get(), not get_or_create() — it must already exist by
# the time this runs (see the migration dependency below); silently
# creating a second, empty one would be a real bug, not idempotency.

from django.db import migrations

ADMINISTRATION_GROUP_NAME = "Administration"
ADMINISTRATION_ADDITIONAL_CODENAMES = ("can_manage_accounts", "can_record_expenses")


def _sync_finance_permissions(apps):
    """On a fresh `migrate` the post_migrate signal that materialises
    custom Meta.permissions rows hasn't fired yet, so the codenames below
    wouldn't exist to attach. Force it now — a no-op (safe to re-run) once
    they exist. Same helper as admissions' 0017 / hr's 0004."""
    from django.contrib.auth.management import create_permissions

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None


def create_accounting_permissions(apps, schema_editor):
    _sync_finance_permissions(apps)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    administration_group = Group.objects.get(name=ADMINISTRATION_GROUP_NAME)
    additional_perms = Permission.objects.filter(
        codename__in=ADMINISTRATION_ADDITIONAL_CODENAMES,
        content_type__app_label="finance",
    )
    administration_group.permissions.add(*additional_perms)


def remove_accounting_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    additional_perms = Permission.objects.filter(
        codename__in=ADMINISTRATION_ADDITIONAL_CODENAMES,
        content_type__app_label="finance",
    )
    administration_group = Group.objects.filter(name=ADMINISTRATION_GROUP_NAME).first()
    if administration_group is not None:
        administration_group.permissions.remove(*additional_perms)


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0003_alter_account_options_alter_expense_options_and_more"),
        ("admissions", "0017_administration_group"),
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_accounting_permissions, remove_accounting_permissions),
    ]
