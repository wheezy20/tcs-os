"""Grade-band coordinator scoping (Phase 6.2). One function is the whole
contract — everything else (admin.py's GradeBandScopedAdmin/Inline mixins,
`manage.py audit_staff_roles`, tests) is built on scoped_grades_for(). Kept
in its own module, separate from admin.py, so it can be imported by the
management command and by tests without pulling in the whole admin module.
"""

from .models import GRADE_BANDS

COORDINATOR_GROUP_BY_BAND = {
    "preschool": "Preschool Coordinator",
    "primary": "Primary Coordinator",
    "jhs": "JHS Coordinator",
}
COORDINATOR_GROUPS = set(COORDINATOR_GROUP_BY_BAND.values())
ADMINISTRATION_GROUP = "Administration"


def scoped_grades_for(user):
    """None      -> unrestricted (superuser / Administration member / any
                    other staff user who isn't a coordinator at all — same
                    un-scoped behaviour this system always had).
       frozenset -> restrict to these `year_group_applied_for` values. An
                    EMPTY frozenset means "sees nothing" — the fail-closed
                    case for a user who's in a coordinator Group but whose
                    StaffProfile.grade_band isn't set (or has no profile at
                    all). A misconfiguration should never silently grant the
                    unrestricted view.

    grade_band is read directly off StaffProfile — never inferred from which
    Coordinator Group the user happens to be in — so the two staying in sync
    is a real (checked, not enforced) admin responsibility. See
    StaffProfile's own docstring and `manage.py audit_staff_roles`."""
    if user.is_superuser or user.groups.filter(name=ADMINISTRATION_GROUP).exists():
        return None

    band = getattr(getattr(user, "staff_profile", None), "grade_band", "")
    if band:
        return GRADE_BANDS[band]

    if user.groups.filter(name__in=COORDINATOR_GROUPS).exists():
        return frozenset()  # in a coordinator group, but no band set — fail closed

    return None  # not a coordinator at all — unrestricted, unchanged from before this feature
