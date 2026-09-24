"""Session 6 — the payroll workflow. Staff-facing, server-rendered HTML
pages (not a JSON API — DRF is deliberately not used here, unlike
admissions/views.py). This is the first staff-facing HTML view in the
project outside Django admin, so it establishes rather than copies a
pattern: plain Django class-based views, Django's own
LoginRequiredMixin + PermissionRequiredMixin for auth (same User/session
admin already uses — no new auth mechanism), gated per-view on
hr.can_process_payroll or hr.can_approve_payroll (see models.py's
PayrollRun.Meta.permissions and migrations/0004_payroll_permissions.py).

Bulk operations (generate payslips) are always per-row loops with
individual error handling, never a queryset.update() or an all-or-
nothing transaction across the whole batch — see CLAUDE.md's "bulk admin
actions bypass save()" and "one bad item can't fail an entire batch"
lessons.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from . import documents, storage
from .models import DOCUMENT_KIND_CHOICES, Employee, EmployeeGeneratedDocument, PayrollRun, PayrollRunLockedError, Payslip
from .payroll import PayrollConfigError, calculate_payslip


class HrStaffRequiredMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Redirects an unauthenticated visitor to the staff login page (same
    one Django admin uses — no separate hr login flow); raises a clean
    403 for an authenticated user who's logged in but lacks the required
    permission, rather than bouncing them back to the same login page
    they're already past (the default PermissionRequiredMixin behaviour,
    which reads as a confusing redirect loop for a real staff user)."""

    login_url = "/admin/login/"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied(self.get_permission_denied_message())
        return super().handle_no_permission()


class PayrollRunCreateView(HrStaffRequiredMixin, View):
    """GET renders the create form; POST creates the run. can_process_payroll
    only — approvers have nothing to do until a run reaches Ready for
    Review."""

    permission_required = "hr.can_process_payroll"

    def get(self, request):
        return render(request, "hr/payroll_run_form.html", {"errors": {}, "values": {}})

    def post(self, request):
        branch = (request.POST.get("branch") or "").strip()
        month = request.POST.get("month")
        year = request.POST.get("year")

        errors = {}
        if not branch:
            errors["branch"] = "Branch is required."
        try:
            month = int(month)
            if not 1 <= month <= 12:
                errors["month"] = "Month must be between 1 and 12."
        except (TypeError, ValueError):
            errors["month"] = "Month is required."
        try:
            year = int(year)
        except (TypeError, ValueError):
            errors["year"] = "Year is required."

        if not errors and PayrollRun.objects.filter(branch=branch, month=month, year=year).exists():
            errors["non_field"] = f"A payroll run already exists for {branch} {month:02d}/{year}."

        if errors:
            return render(
                request, "hr/payroll_run_form.html",
                {"errors": errors, "values": {"branch": branch, "month": month, "year": year}},
            )

        try:
            run = PayrollRun.objects.create(branch=branch, month=month, year=year)
        except IntegrityError:
            # Belt-and-suspenders against a race between the exists() check
            # above and the insert — the unique_together constraint is the
            # real backstop, this just keeps the failure mode a clean
            # message instead of a raw 500.
            return render(
                request, "hr/payroll_run_form.html",
                {
                    "errors": {"non_field": f"A payroll run already exists for {branch} {month:02d}/{year}."},
                    "values": {"branch": branch, "month": month, "year": year},
                },
            )

        return redirect("hr:payroll-run-detail", pk=run.pk)


class PayrollRunDetailView(HrStaffRequiredMixin, View):
    """Viewable by either role — a processor needs it to generate
    payslips and submit; an approver needs it to see what they're
    approving or rejecting. Deliberately overrides has_permission() to
    check for ANY of the two permissions, not PermissionRequiredMixin's
    default all-of-them-required behaviour (which would make no sense
    for a two-entry permission_required here)."""

    permission_required = ("hr.can_process_payroll", "hr.can_approve_payroll")

    def has_permission(self):
        return any(self.request.user.has_perm(perm) for perm in self.get_permission_required())

    def get(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)

        # Employees not yet on this run. "Active EmployeePayConfig" (per
        # the original spec) isn't a real field — the actual eligibility
        # check (approved + effective for the run's period) is exactly
        # what calculate_payslip() already resolves via
        # _get_effective_pay_config(); re-implementing that query here
        # would risk drifting out of sync with the one source of truth
        # tested in Session 5. So this list is deliberately broader than
        # "definitely payable" — it's every active employee not already
        # on the run — and PayrollConfigError at generation time is what
        # actually screens out anyone without an eligible config, with a
        # visible per-employee reason.
        candidate_employees = Employee.objects.filter(employment_status="active").exclude(
            payslips__payroll_run=run
        )

        return render(
            request, "hr/payroll_run_detail.html",
            {
                "run": run,
                "candidate_employees": candidate_employees,
                "payslips": run.payslips.select_related("employee").all(),
                "can_process": request.user.has_perm("hr.can_process_payroll"),
                "can_approve": request.user.has_perm("hr.can_approve_payroll"),
            },
        )


class PayrollRunGeneratePayslipsView(HrStaffRequiredMixin, View):
    """POST-only bulk action: generate a Payslip for each selected
    employee. Only valid on a Draft run. Never a queryset.update() or an
    all-employees-or-none transaction — one employee's PayrollConfigError
    (no approved config effective for this period) skips just that
    employee, with a clear reason, and the rest of the batch still
    completes."""

    permission_required = "hr.can_process_payroll"

    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        if run.status != "draft":
            return render(
                request, "hr/payroll_run_generate_result.html",
                {
                    "run": run, "generated": [], "skipped": [],
                    "error": f'Cannot generate payslips — "{run}" is not a Draft run.',
                },
            )

        employee_ids = request.POST.getlist("employee_id")
        employees = Employee.objects.filter(pk__in=employee_ids, employment_status="active")

        generated = []
        skipped = []
        for employee in employees:
            if Payslip.objects.filter(payroll_run=run, employee=employee).exists():
                continue  # already on this run — not an error, just nothing to do
            try:
                data = calculate_payslip(employee, run)
            except PayrollConfigError as exc:
                skipped.append((employee, str(exc)))
                continue
            try:
                Payslip.objects.create(payroll_run=run, employee=employee, **data)
            except PayrollRunLockedError:
                # The run was posted by a concurrent request partway
                # through this batch (e.g. another approver's Approve &
                # Post landing mid-loop) — stop generating rather than
                # let the model-level lock raise as an unhandled 500,
                # and report what actually happened instead of pretending
                # the rest of the batch was attempted.
                return render(
                    request, "hr/payroll_run_generate_result.html",
                    {
                        "run": run, "generated": generated, "skipped": skipped,
                        "error": f'"{run}" was posted by someone else while this batch was running — '
                        f"stopped after {len(generated)} generated.",
                    },
                )
            generated.append(employee)

        return render(
            request, "hr/payroll_run_generate_result.html",
            {"run": run, "generated": generated, "skipped": skipped},
        )


class PayrollRunSubmitView(HrStaffRequiredMixin, View):
    """POST-only. Draft -> Ready for Review. Requires at least one
    payslip on the run — submitting an empty run for approval makes no
    sense."""

    permission_required = "hr.can_process_payroll"

    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        if run.status != "draft":
            return render(
                request, "hr/payroll_run_action_result.html",
                {"run": run, "error": f'Cannot submit — "{run}" is not a Draft run.'},
            )
        if not run.payslips.exists():
            return render(
                request, "hr/payroll_run_action_result.html",
                {"run": run, "error": f'Cannot submit "{run}" — it has no payslips yet.'},
            )

        run.status = "ready_for_review"
        run.save()
        return redirect("hr:payroll-run-detail", pk=run.pk)


class PayrollRunApproveView(HrStaffRequiredMixin, View):
    """POST-only. Ready for Review -> Posted. Posted means locked only —
    no journal-entry/Finance posting here, that's Merge Phase 2 and
    explicitly out of scope for this session."""

    permission_required = "hr.can_approve_payroll"

    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        if run.status != "ready_for_review":
            return render(
                request, "hr/payroll_run_action_result.html",
                {"run": run, "error": f'Cannot approve — "{run}" is not Ready for Review.'},
            )

        run.status = "posted"
        run.save()
        return redirect("hr:payroll-run-detail", pk=run.pk)


class PayrollRunRejectView(HrStaffRequiredMixin, View):
    """POST-only. Ready for Review -> Draft, with a required reason
    (PayrollRun.rejection_reason) so the processor knows what to fix."""

    permission_required = "hr.can_approve_payroll"

    def post(self, request, pk):
        run = get_object_or_404(PayrollRun, pk=pk)
        if run.status != "ready_for_review":
            return render(
                request, "hr/payroll_run_action_result.html",
                {"run": run, "error": f'Cannot reject — "{run}" is not Ready for Review.'},
            )

        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            return render(
                request, "hr/payroll_run_detail.html",
                {
                    "run": run,
                    "candidate_employees": Employee.objects.none(),
                    "payslips": run.payslips.select_related("employee").all(),
                    "can_process": request.user.has_perm("hr.can_process_payroll"),
                    "can_approve": request.user.has_perm("hr.can_approve_payroll"),
                    "reject_error": "A reason is required to reject a payroll run.",
                },
            )

        run.status = "draft"
        run.rejection_reason = reason
        run.save()
        return redirect("hr:payroll-run-detail", pk=run.pk)


# --- Session 7: employee-generated documents ---
#
# Reuses hr.can_process_payroll (the Session 6 permission) rather than
# minting a new one — this is HR-administrative work, not payroll
# approval specifically, but a dedicated permission for one feature
# wasn't judged to be justified. All five views below share that gate.

class EmployeeDetailView(HrStaffRequiredMixin, View):
    """A minimal, read-only employee profile page (name, number, status,
    position, department — no edit form; editing an Employee stays an
    admin task for now) with the Documents section from the spec living
    on it. Built specifically because no Employee-centric page existed
    anywhere in the project before this session — Session 6 only built
    PayrollRun-centric views."""

    permission_required = "hr.can_process_payroll"

    def get(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        return render(
            request, "hr/employee_detail.html",
            {
                "employee": employee,
                "document_kinds": DOCUMENT_KIND_CHOICES,
                "contract_kinds": documents.CONTRACT_KINDS,
                "generated_documents": employee.generated_documents.all(),
            },
        )


class EmployeeDocumentGenerateView(HrStaffRequiredMixin, View):
    permission_required = "hr.can_process_payroll"

    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        document_kind = request.POST.get("document_kind")
        contract_variant = request.POST.get("contract_variant") or None
        manual_probation_months = request.POST.get("manual_probation_months") or None
        if manual_probation_months is not None:
            try:
                manual_probation_months = int(manual_probation_months)
            except ValueError:
                manual_probation_months = None

        try:
            documents.generate_document(
                employee, document_kind, request.user,
                contract_variant=contract_variant, manual_probation_months=manual_probation_months,
            )
        except (documents.DocumentGenerationError, storage.HrStorageError) as exc:
            return _render_employee_detail_with_error(request, employee, str(exc))

        return redirect("hr:employee-detail", pk=employee.pk)


def _render_employee_detail_with_error(request, employee, error):
    return render(
        request, "hr/employee_detail.html",
        {
            "employee": employee,
            "document_kinds": DOCUMENT_KIND_CHOICES,
            "contract_kinds": documents.CONTRACT_KINDS,
            "generated_documents": employee.generated_documents.all(),
            "error": error,
        },
    )


class EmployeeGeneratedDocumentIssueView(HrStaffRequiredMixin, View):
    permission_required = "hr.can_process_payroll"

    def post(self, request, pk):
        document = get_object_or_404(EmployeeGeneratedDocument, pk=pk)
        try:
            documents.issue_document(document, request.user)
        except documents.DocumentLifecycleError as exc:
            return _render_employee_detail_with_error(request, document.employee, str(exc))
        return redirect("hr:employee-detail", pk=document.employee_id)


class EmployeeGeneratedDocumentDiscardView(HrStaffRequiredMixin, View):
    permission_required = "hr.can_process_payroll"

    def post(self, request, pk):
        document = get_object_or_404(EmployeeGeneratedDocument, pk=pk)
        employee = document.employee
        try:
            documents.discard_document(document)
        except documents.DocumentLifecycleError as exc:
            return _render_employee_detail_with_error(request, employee, str(exc))
        return redirect("hr:employee-detail", pk=employee.pk)


class EmployeeGeneratedDocumentRecordAcceptanceView(HrStaffRequiredMixin, View):
    permission_required = "hr.can_process_payroll"

    def post(self, request, pk):
        document = get_object_or_404(EmployeeGeneratedDocument, pk=pk)
        signature_name = request.POST.get("signature_name", "")
        try:
            documents.record_acceptance(document, signature_name)
        except (documents.DocumentLifecycleError, documents.DocumentGenerationError) as exc:
            return _render_employee_detail_with_error(request, document.employee, str(exc))
        return redirect("hr:employee-detail", pk=document.employee_id)


class EmployeeGeneratedDocumentViewLinkView(HrStaffRequiredMixin, View):
    """Mints a fresh signed URL and redirects to it — never stores or
    caches one, same convention as admissions' document links."""

    permission_required = "hr.can_process_payroll"

    def get(self, request, pk):
        document = get_object_or_404(EmployeeGeneratedDocument, pk=pk)
        try:
            url = storage.create_read_url(document.storage_path)
        except storage.HrStorageError as exc:
            return _render_employee_detail_with_error(request, document.employee, str(exc))
        return redirect(url)
