from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from admissions.access import ADMINISTRATION_GROUP, COORDINATOR_GROUP_BY_BAND, COORDINATOR_GROUPS


class Command(BaseCommand):
    """Phase 6.2 — StaffProfile.grade_band (the authoritative scope) and
    Coordinator Group membership (the permission bundle) are two separate
    things that are supposed to agree, but nothing in the schema enforces
    that. This is the check: run it after any roster change (new hire, band
    reassignment, someone leaving Administration) — not wired into `check`
    or CI, since it hits the DB and role drift isn't a deploy-blocking
    condition, just something a human should notice."""

    help = (
        "Flags staff users whose Coordinator Group membership and "
        "StaffProfile.grade_band disagree, or who are in Administration "
        "and a Coordinator group at once (Administration wins; the "
        "coordinator scope is silently ignored)."
    )

    def handle(self, *args, **options):
        problems = []
        users = (
            User.objects.filter(is_staff=True)
            .select_related("staff_profile")
            .prefetch_related("groups")
            .order_by("username")
        )

        for user in users:
            group_names = {g.name for g in user.groups.all()}
            coordinator_groups = group_names & COORDINATOR_GROUPS
            is_admin = ADMINISTRATION_GROUP in group_names
            profile = getattr(user, "staff_profile", None)
            band = profile.grade_band if profile else ""

            if coordinator_groups and is_admin:
                problems.append(
                    f"{user.username}: in Administration AND {sorted(coordinator_groups)} — "
                    "Administration wins, the coordinator scope is ignored."
                )

            if coordinator_groups and not band:
                problems.append(
                    f"{user.username}: in {sorted(coordinator_groups)} but StaffProfile.grade_band "
                    "is unset — fails closed, sees NOTHING."
                )

            if band and not is_admin:
                expected_group = COORDINATOR_GROUP_BY_BAND[band]
                if expected_group not in group_names:
                    problems.append(
                        f"{user.username}: grade_band='{band}' but not in '{expected_group}' — "
                        "has scope with none of the coordinator permissions."
                    )
                mismatched = coordinator_groups - {expected_group}
                if mismatched:
                    problems.append(
                        f"{user.username}: grade_band='{band}' but also in {sorted(mismatched)} "
                        "(a different band's group)."
                    )

        if not problems:
            self.stdout.write(self.style.SUCCESS("No staff-role inconsistencies found."))
            return

        self.stdout.write(self.style.WARNING(f"{len(problems)} staff-role inconsistenc{'y' if len(problems) == 1 else 'ies'}:"))
        for problem in problems:
            self.stdout.write(f"  - {problem}")
