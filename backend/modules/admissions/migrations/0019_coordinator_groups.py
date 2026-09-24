# Phase 6.2 — the three grade-band coordinator Groups. All three get the
# IDENTICAL permission bundle below, including can_view_health_info (Option B,
# approved 2026-09-04: health visibility for all bands, scoped to each
# coordinator's own band via GradeBandScopedInline on HealthInfoInline — see
# docs/admissions/02-stack-and-schema.md). What actually differs per
# coordinator is their StaffProfile.grade_band, not their Group's
# permissions — see admissions/access.py.

from django.db import migrations

GROUP_NAMES = ("Preschool Coordinator", "Primary Coordinator", "JHS Coordinator")

PERMISSION_CODENAMES = (
    "view_application", "change_application",
    "view_document", "change_document",
    "view_note", "add_note", "change_note",
    "view_emergencycontact",
    "view_student", "view_family", "view_guardian",
    "can_view_health_info",
)


def _sync_admissions_permissions(apps):
    """Same fresh-DB guard as 0017_administration_group — the post_migrate
    signal that materialises custom Meta.permissions rows hasn't fired yet at
    data-migration time on a brand new `migrate`. No-op on an existing DB
    where they already exist; safe to re-run."""
    from django.contrib.auth.management import create_permissions

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None


def create_coordinator_groups(apps, schema_editor):
    _sync_admissions_permissions(apps)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    perms = Permission.objects.filter(
        codename__in=PERMISSION_CODENAMES,
        content_type__app_label="admissions",
    )
    for name in GROUP_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        # set() so re-running reconciles exactly to this list rather than piling on.
        group.permissions.set(perms)


def delete_coordinator_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=GROUP_NAMES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("admissions", "0018_staffprofile"),
    ]

    operations = [
        migrations.RunPython(create_coordinator_groups, delete_coordinator_groups),
    ]
