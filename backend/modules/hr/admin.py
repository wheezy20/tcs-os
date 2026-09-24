"""Admin registration for the hr app. Follows the same conventions as
modules/admissions/admin.py: unfold's ModelAdmin (not plain
django.contrib.admin.ModelAdmin), @admin.register, and the
"computed/generated data is read-only in admin" pattern already used there
for TransactionalEmail.

RBAC note (flagged, not decided here — see the module's admin.py precedent,
where a real-blast-radius permission like admissions.can_decide or
admissions.can_view_health_info gets its own Meta.permissions entry and a
deliberate Group grant, per docs/CONSTRAINTS.md's "never auto-granted" rule):
payroll data (basic_salary, PAYEBand, StatutoryRate, generated Payslips) is
at least as sensitive as anything gated that way in admissions, and right
now everything here relies only on default Django admin permissions
(is_staff + the model's own add/change/view/delete). Worth an explicit
decision on whether "who can see/edit payroll" needs its own permission
(e.g. hr.can_manage_payroll) bundled into a dedicated Group, the same way
Administration/Coordinator groups work in admissions — not something to
default into silently.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import (
    AllowanceType, ContractTemplate, Employee, EmployeeGeneratedDocument, EmployeePayConfig, PAYEBand, Payslip,
    PayrollRun, StatutoryRate,
)


@admin.register(Employee)
class EmployeeAdmin(ModelAdmin):
    """position/department (flagged as missing back in Session 4's
    EmployeeAdmin, added to the model in Session 7) are now real fields —
    included here."""

    list_display = ("full_name", "employee_number", "position", "department", "employment_status")
    list_filter = ("employment_status", "department")
    search_fields = ("first_name", "surname", "employee_number")


@admin.register(EmployeePayConfig)
class EmployeePayConfigAdmin(ModelAdmin):
    """Effective-dated config (see docs/DESIGN.md) — deliberately no custom
    admin action that bulk-edits a row in place. The live, in-use row for an
    employee is the one with effective_to=null and approval_status=approved
    (see models.py's docstring); normal single-row editing via the change
    form is still available (that's standard admin, not a bulk action), but
    nothing here offers a queryset.update()-style action that could mutate
    an in-force config as a side effect of something else."""

    list_display = ("employee", "basic_salary", "effective_from", "effective_to", "approval_status")
    list_filter = ("approval_status",)
    search_fields = ("employee__first_name", "employee__surname", "employee__employee_number")


@admin.register(PayrollRun)
class PayrollRunAdmin(ModelAdmin):
    list_display = ("branch", "month", "year", "status")
    list_filter = ("status", "year")


@admin.register(Payslip)
class PayslipAdmin(ModelAdmin):
    """Generated, never hand-entered — same "computed, never manually
    edited" treatment as admissions.TransactionalEmailAdmin (its cited
    precedent), which also disables add/change/delete outright rather than
    just hiding the add button."""

    list_display = ("employee", "payroll_run", "gross_salary", "net_pay")
    search_fields = ("employee__first_name", "employee__surname", "employee__employee_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AllowanceType)
class AllowanceTypeAdmin(ModelAdmin):
    list_display = ("name", "taxable")


_STATUTORY_ACCURACY_NOTE = (
    "Editable because a new tax year means adding a new dated row here — but "
    "any change to this table must be re-verified against docs/CONSTRAINTS.md's "
    "\"Statutory accuracy (payroll, HR module)\" checklist and docs/DESIGN.md's "
    "payroll section before it's trusted, not just entered. Getting this wrong "
    "silently changes every payslip calculated from the date this becomes effective."
)


@admin.register(PAYEBand)
class PAYEBandAdmin(ModelAdmin):
    list_display = ("band_order", "lower_bound", "upper_bound", "rate", "effective_from")
    list_filter = ("effective_from",)
    ordering = ("-effective_from", "band_order")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["effective_from"].help_text = (
            f"{form.base_fields['effective_from'].help_text or ''} {_STATUTORY_ACCURACY_NOTE}".strip()
        )
        return form


@admin.register(ContractTemplate)
class ContractTemplateAdmin(ModelAdmin):
    """The one place html_body actually gets authored — editable on
    purpose (see the model's own docstring: seeded blank, a human has to
    write real letter/contract text here before that kind can be
    generated). updated_by auto-populates on save rather than being a
    manually-set field, so it always reflects who actually edited it."""

    list_display = ("category", "updated_by", "updated_at")
    readonly_fields = ("updated_by", "updated_at", "created_at")

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(EmployeeGeneratedDocument)
class EmployeeGeneratedDocumentAdmin(ModelAdmin):
    """Generated by documents.py's workflow (generate_document() /
    issue_document() / record_acceptance()), never hand-created or
    hand-edited in admin — same fully-locked "computed, never manually
    edited" treatment as PayslipAdmin above and TransactionalEmailAdmin
    in admissions. Visibility/audit only."""

    list_display = ("employee", "document_kind", "version", "status", "issued_at")
    list_filter = ("document_kind", "status")
    search_fields = ("employee__first_name", "employee__surname", "employee__employee_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StatutoryRate)
class StatutoryRateAdmin(ModelAdmin):
    list_display = (
        "effective_from", "ssnit_employee_pct", "ssnit_employer_pct", "tier2_employee_pct",
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["effective_from"].help_text = (
            f"{form.base_fields['effective_from'].help_text or ''} {_STATUTORY_ACCURACY_NOTE}".strip()
        )
        return form
