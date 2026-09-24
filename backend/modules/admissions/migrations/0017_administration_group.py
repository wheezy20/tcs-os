# b5 — the "Administration" role. A single Django Group so onboarding a senior
# staff member is "add them to Administration" rather than ticking individual
# boxes. See docs/deployment.md's admin-setup notes.
#
# Updated 2026-09-05 (Phase 6.2 follow-up), then widened 2026-09-09: grants
# every permission a non-superuser Administration member needs for full
# functional access *within admissions* — view/add/change on the core models,
# view on the read-only/audit surfaces. Before this it carried only the 3
# custom permissions, so a non-superuser member was blocked by Django's own
# has_*_permission checks all over the admin (couldn't see an Application,
# couldn't add a Note, couldn't see the bulk-send audit trail, ...). Every
# real Administration user so far has also been a superuser, which silently
# masked all of it. From here on "Administration" means full admissions
# access on its own — NOT "requires Django-wide superuser".
#
# Deliberately NOT granted (documented so it's a decision, not an oversight):
#   * every delete_* — row cleanup (duplicate guardian, spam Lead, junk draft
#     campaign) stays a superuser task on purpose.
#   * all auth.* (users, groups, permissions) — Administration runs
#     admissions, it does not administer staff accounts. Creating a Django
#     user / assigning groups / setting passwords stays a superuser task
#     (see docs/deployment.md).
#   * django plumbing (sessions, content types, admin log, StaffProfile —
#     the last only reachable via the User admin, which needs auth.change_user).
#
# A few add_* below are inert because the relevant admin/inline hard-codes
# has_add_permission (Offer, Lead) or gates it on a custom permission the
# group already has (Decision -> can_decide, HealthInfo -> can_view_health_info).
# Granted anyway so this list reads as a clean "all of admissions, minus
# delete" rather than a bespoke subset that's harder to reason about.
#
# This migration was NOT yet applied anywhere but ephemeral test databases at
# the time of these edits (docs/deployment.md still listed 0017 as pending),
# so it's edited in place rather than layered under a new migration number.

from django.db import migrations

GROUP_NAME = "Administration"

CUSTOM_PERMISSION_CODENAMES = ("can_decide", "can_view_health_info", "can_send_bulk_email")

# view + add + change (never delete) — the models an Administration member
# creates and edits in normal admissions work.
CRU_MODELS = (
    "application", "student", "family", "guardian", "document", "note",
    "emergencycontact", "healthinfo", "decision", "offer", "lead",
    "emailcampaign", "capacity", "campus",
)

# view only — read-only / audit / support surfaces. Each of these admin pages
# (and, for TransactionalEmail, its resend_failed action) is invisible
# without the view permission; none is ever hand-created or hand-edited.
VIEW_ONLY_MODELS = (
    "applicationdraft", "referencecounter", "transactionalemail", "emailcampaignrecipient",
)


def _build_codenames():
    codenames = set(CUSTOM_PERMISSION_CODENAMES)
    for model in CRU_MODELS:
        codenames |= {f"view_{model}", f"add_{model}", f"change_{model}"}
    for model in VIEW_ONLY_MODELS:
        codenames.add(f"view_{model}")
    return codenames


PERMISSION_CODENAMES = _build_codenames()


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
