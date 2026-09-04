# b5 — the "Administration" role. A single Django Group bundling the three
# deliberately-not-auto-granted custom admissions permissions, so onboarding a
# senior staff member is "add them to Administration" rather than ticking
# individual boxes. See docs/deployment.md's admin-setup notes.
#
# Updated 2026-09-05 (Phase 6.2 follow-up): also grants explicit base
# view/change permissions on every admissions model a coordinator or staff
# member might touch. Before this, "Administration" carried only the 3 custom
# permissions below — a non-superuser member would still be blocked by
# Django's own has_view_permission/has_change_permission for lack of e.g.
# view_application. Every real Administration user so far has also been a
# superuser, which silently masked this. From here on, "Administration" means
# full functional access *within admissions* on its own — not "requires
# Django-wide superuser" — so a future non-superuser senior hire works
# correctly. Deliberately still NOT add_*/delete_* on this list (see
# docs/admissions/02-stack-and-schema.md's Phase 6.2 section for the two
# known, flagged consequences of that: a non-superuser Administration member
# can't add a Note via the inline, or see the EmailCampaignRecipient audit
# inline, without also being a superuser).
#
# This migration was NOT yet applied anywhere but ephemeral test databases at
# the time of this edit (see docs/deployment.md's "Current deployment state"
# — 0017 was still listed as pending), so it's edited in place rather than
# layered under a new migration number.

from django.db import migrations

GROUP_NAME = "Administration"

CUSTOM_PERMISSION_CODENAMES = ("can_decide", "can_view_health_info", "can_send_bulk_email")

# Base view/change Django permissions — deliberately not add_*/delete_* (see
# the module docstring above). Every admissions model an Administration
# member might reasonably need to see or edit day to day.
CRUD_MODELS = (
    "application", "student", "family", "guardian", "document", "note",
    "emergencycontact", "healthinfo", "decision", "offer", "lead",
    "emailcampaign", "capacity", "campus",
)
CRUD_PERMISSION_CODENAMES = tuple(
    f"{action}_{model}" for model in CRUD_MODELS for action in ("view", "change")
)

PERMISSION_CODENAMES = CUSTOM_PERMISSION_CODENAMES + CRUD_PERMISSION_CODENAMES


def _sync_admissions_permissions(apps):
    """On a fresh `migrate` the post_migrate signal that materialises custom
    Meta.permissions rows hasn't fired yet, so the codenames below wouldn't
    exist to attach. Force it now. A no-op on an existing DB where they're
    already present (this migration is safe to re-run)."""
    from django.contrib.auth.management import create_permissions

    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None


def create_administration_group(apps, schema_editor):
    _sync_admissions_permissions(apps)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group, _ = Group.objects.get_or_create(name=GROUP_NAME)
    perms = Permission.objects.filter(
        codename__in=PERMISSION_CODENAMES,
        content_type__app_label="admissions",
    )
    # set() so re-running reconciles exactly to this list rather than piling on.
    group.permissions.set(perms)


def delete_administration_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("admissions", "0016_lead_utm_fields"),
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_administration_group, delete_administration_group),
    ]
