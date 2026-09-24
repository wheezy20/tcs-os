# Session 6 — payroll workflow permissions. Follows
# admissions/migrations/0017_administration_group.py's idempotent pattern
# exactly, with one deliberate deviation flagged below.
#
# Two custom permissions on PayrollRun (added by 0003, this migration just
# attaches them to Groups): can_process_payroll and can_approve_payroll.
#
#   * "Payroll Processor" — a new Group, granted can_process_payroll only.
#     For a future Accountant-equivalent role. Nobody is auto-assigned to
#     it — populating it with real users is always a deliberate onboarding
#     decision (docs/CONSTRAINTS.md's "never auto-granted" rule), same as
#     every other real-blast-radius permission in this project.
#   * "Administration" (the existing admissions Group — see
#     admissions/migrations/0017) — granted BOTH can_process_payroll and
#     can_approve_payroll, so it stays "full non-destructive access"
#     without needing a separate approver Group. can_approve_payroll is
#     deliberately granted to Administration only, never to Payroll
#     Processor — approval stays admin/manager-only, not delegable.
#
# DEVIATION FROM 0017's PATTERN, intentional — read before "fixing" this
# back to match 0017 exactly: 0017 uses group.permissions.set(...) because
# it owns admissions' ENTIRE permission list for the Administration group.
# This migration does NOT own that list — Administration already carries a
# full set of admissions permissions from 0017, and .set() here would wipe
# every one of them, leaving only these two hr permissions. So this
# migration uses .add() on Administration (additive, leaves 0017's grants
# untouched) and reverses with .remove() (removes only these two
# codenames, not the whole membership). "Payroll Processor" is the one
# Group this migration DOES fully own, so it uses .set() there, same as
# 0017.
#
# Administration is looked up with .get(), not get_or_create() — the
# explicit migration dependency on admissions' 0017 guarantees it already
# exists by the time this runs; silently creating a second, empty
# "Administration" group here (e.g. if 0017 hadn't run yet) would be a
# real bug, not idempotency, so this deliberately fails loudly instead.
#
# Like 0017, migrations don't import another app's live module even when a
# shared constant exists (see admissions/access.py's ADMINISTRATION_GROUP)
# — the group name is hardcoded here too, matching 0017/0019's own
# precedent of self-contained migrations.

from django.db import migrations

PROCESSOR_GROUP_NAME = "Payroll Processor"
ADMINISTRATION_GROUP_NAME = "Administration"

PROCESSOR_CODENAMES = ("can_process_payroll",)
ADMINISTRATION_ADDITIONAL_CODENAMES = ("can_process_payroll", "can_approve_payroll")


def _sync_hr_permissions(apps):
    """On a fresh `migrate` the post_migrate signal that materialises
    custom Meta.permissions rows hasn't fired yet, so the codenames below
    wouldn't exist to attach. Force it now — a no-op (safe to re-run) once
    they exist. Same helper as admissions/migrations/0017's own."""
    from django.contrib.auth.management import create_permissions

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None


def create_payroll_permissions(apps, schema_editor):
    _sync_hr_permissions(apps)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    processor_group, _ = Group.objects.get_or_create(name=PROCESSOR_GROUP_NAME)
    processor_perms = Permission.objects.filter(
        codename__in=PROCESSOR_CODENAMES,
        content_type__app_label="hr",
    )
    # set(): this migration owns "Payroll Processor"'s entire permission
    # list, so re-running reconciles exactly to it, same as 0017.
    processor_group.permissions.set(processor_perms)

    # Administration must already exist — see the module docstring on why
    # this is .get(), not get_or_create().
    administration_group = Group.objects.get(name=ADMINISTRATION_GROUP_NAME)
    administration_additional_perms = Permission.objects.filter(
        codename__in=ADMINISTRATION_ADDITIONAL_CODENAMES,
        content_type__app_label="hr",
    )
    # add(), NOT set() — Administration's admissions permissions (granted
    # by 0017) are owned by that migration, not this one. .set() here
    # would silently wipe them. See the module docstring.
    administration_group.permissions.add(*administration_additional_perms)


def remove_payroll_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    Group.objects.filter(name=PROCESSOR_GROUP_NAME).delete()

    administration_additional_perms = Permission.objects.filter(
        codename__in=ADMINISTRATION_ADDITIONAL_CODENAMES,
        content_type__app_label="hr",
    )
    administration_group = Group.objects.filter(name=ADMINISTRATION_GROUP_NAME).first()
    if administration_group is not None:
        administration_group.permissions.remove(*administration_additional_perms)


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0003_alter_payrollrun_options_payrollrun_rejection_reason"),
        ("admissions", "0017_administration_group"),
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_payroll_permissions, remove_payroll_permissions),
    ]
