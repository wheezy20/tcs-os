"""Shared base for staff-facing HTML views (not DRF, not admin) — first
built for hr's payroll workflow (Session 6), extracted here in finance
Session 3 so a second module doesn't duplicate it, same "extract shared
code, don't cross-import between module apps" pattern as
tcs_os/text_merge.py and tcs_os/reference_counter.py.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied


class StaffRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Redirects an unauthenticated visitor to the staff login page (the
    same one Django admin uses — no separate per-module login flow);
    raises a clean 403 for an authenticated user who's logged in but
    lacks the required permission, rather than bouncing them back to the
    same login page they're already past (the default
    PermissionRequiredMixin behaviour, which reads as a confusing
    redirect loop for a real staff user)."""

    login_url = "/admin/login/"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied(self.get_permission_denied_message())
        return super().handle_no_permission()
