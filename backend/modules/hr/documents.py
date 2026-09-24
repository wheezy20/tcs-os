"""Employee-generated documents (Session 7) — Appointment Letter,
Probation Letter, Contract-Teaching, Contract-Non-Teaching. Ports the
ERP's employee_generated_documents design (one generalized lifecycle
table, not one table per kind — see models.py's DOCUMENT_KIND_CHOICES
and EmployeeGeneratedDocument) with server-side PDF rendering
(WeasyPrint) instead of the ERP's client-side html2canvas/jsPDF, per
docs/DESIGN.md's "Employee-generated documents" section.

Lifecycle: generate_document() creates a Draft; issue_document() promotes
it to Issued (demoting any prior Issued row for the same employee+kind to
Superseded FIRST — required for the DB partial unique constraint to
accept the promotion); discard_document() deletes a Draft outright (a
Draft was never real — nothing to preserve a history of, unlike an
Issued/Superseded row); record_acceptance() is HR-recorded, not
self-service signing.
"""

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from weasyprint import HTML

from tcs_os.text_merge import render_template

from . import storage
from .models import ContractTemplate, EmployeeGeneratedDocument

CONTRACT_KINDS = {"Contract-Teaching", "Contract-Non-Teaching"}


class DocumentGenerationError(Exception):
    """Raised by generate_document()/build_merge_data() when the document
    can't be generated as requested — a blank ContractTemplate, a missing
    contract_variant for a Contract kind, or a required merge field with
    no value and no manual override. Never silently renders a document
    with a missing/None field baked into real letterhead text — see
    docs/CONSTRAINTS.md's "generated document must be correct, not just
    present" lesson."""


class DocumentLifecycleError(Exception):
    """Raised when a lifecycle transition (issue/discard/accept) is
    attempted from the wrong status — e.g. issuing something already
    Issued, or discarding something already Superseded."""


def _months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def build_merge_data(employee, document_kind, manual_probation_months=None):
    """Assembles the exact merge-field dict a ContractTemplate.html_body
    is rendered against. Raises DocumentGenerationError rather than
    substituting an empty string for a field with no real value — a
    generated Appointment Letter with a blank start date is wrong, not
    merely incomplete."""
    required_employee_fields = {
        "position": employee.position,
        "department": employee.department,
        "employment_type": employee.employment_type,
        "start_date": employee.start_date,
        "basic_salary": employee.basic_salary,
        "payment_method": employee.payment_method,
    }
    missing = [name for name, value in required_employee_fields.items() if not value and value != 0]
    if missing:
        raise DocumentGenerationError(
            f"{employee} is missing required field(s) for document generation: {', '.join(missing)}. "
            f"Set these on the Employee record before generating."
        )

    merge_data = dict(settings.HR_DOCUMENT_LETTERHEAD)
    # HR_DOCUMENT_LETTERHEAD's company_address carries a real embedded
    # "\n" between the street address and the P.O. Box line (confirmed
    # value, not altering the setting itself) — a plain HTML body isn't
    # a <pre>, so that newline collapses to a single space when rendered
    # unless converted to a real line break here, at substitution time.
    # Caught by actually opening a generated PDF (docs/CONSTRAINTS.md's
    # "open and look, don't trust magic bytes" lesson) rather than
    # assumed to be fine.
    merge_data["company_address"] = merge_data["company_address"].replace("\n", "<br>")
    merge_data["issue_date"] = timezone.now().date().isoformat()
    merge_data["employee_name"] = employee.full_name
    merge_data["position"] = employee.position
    merge_data["department"] = employee.department
    merge_data["employment_type"] = employee.employment_type
    merge_data["start_date"] = employee.start_date.isoformat()
    merge_data["probation_end_date"] = employee.probation_end_date.isoformat() if employee.probation_end_date else ""
    merge_data["basic_salary"] = str(Decimal(employee.basic_salary))
    merge_data["payment_method"] = employee.payment_method

    if document_kind == "Probation Letter":
        if employee.start_date and employee.probation_end_date:
            merge_data["probation_duration_months"] = _months_between(
                employee.start_date, employee.probation_end_date
            )
        elif manual_probation_months is not None:
            merge_data["probation_duration_months"] = manual_probation_months
        else:
            raise DocumentGenerationError(
                f"{employee} has no start_date/probation_end_date set — "
                f"probation_duration_months must be entered manually for a Probation Letter."
            )

    return merge_data


def render_document_pdf(html_body, merge_data):
    """Whitelist-substitutes merge_data into html_body (never Django's
    real template engine — see tcs_os.text_merge.render_template's own
    docstring), then renders the resulting HTML to PDF bytes."""
    rendered_html = render_template(html_body, merge_data)
    return HTML(string=rendered_html).write_pdf()


def generate_document(employee, document_kind, created_by, contract_variant=None, manual_probation_months=None):
    """Creates a new Draft EmployeeGeneratedDocument. contract_variant
    ("Teaching" or "Non-Teaching") is required when document_kind is
    Contract-Teaching/Contract-Non-Teaching — it's really just choosing
    WHICH of the two Contract kinds to generate, not a separate axis, so
    document_kind itself must already match the variant; this parameter
    exists so a caller can pass a plain "Teaching"/"Non-Teaching" choice
    and have it validated against document_kind rather than trusted
    silently."""
    if document_kind in CONTRACT_KINDS:
        expected_variant = "Teaching" if document_kind == "Contract-Teaching" else "Non-Teaching"
        if contract_variant != expected_variant:
            raise DocumentGenerationError(
                f"document_kind={document_kind!r} requires contract_variant={expected_variant!r}, "
                f"got {contract_variant!r}."
            )

    try:
        template = ContractTemplate.objects.get(category=document_kind)
    except ContractTemplate.DoesNotExist:
        raise DocumentGenerationError(f"No ContractTemplate exists for {document_kind!r}.")
    if not template.html_body:
        raise DocumentGenerationError(
            f'The "{document_kind}" template has no content yet — an HR staff member must author '
            f"it in admin before this kind can be generated."
        )

    merge_data = build_merge_data(employee, document_kind, manual_probation_months=manual_probation_months)
    pdf_bytes = render_document_pdf(template.html_body, merge_data)

    # The Supabase upload is a real external call, not something a DB
    # transaction can roll back — do it OUTSIDE transaction.atomic() so a
    # failed upload never leaves an uncommitted-but-still-locking write
    # hanging, and so the row we create afterward always points at a file
    # that genuinely exists.
    storage_path = storage.upload_pdf_bytes(employee, document_kind, pdf_bytes)

    try:
        with transaction.atomic():
            # select_for_update() locks existing rows for this
            # employee+kind, so two concurrent calls serialize on the
            # SECOND+ generation for the same employee+kind. It can't
            # lock anything on the FIRST-ever generation (no row exists
            # yet to lock) — the UniqueConstraint on (employee,
            # document_kind, version) is the real backstop for that case,
            # caught below rather than left to surface as a raw 500.
            last_version = (
                EmployeeGeneratedDocument.objects.select_for_update()
                .filter(employee=employee, document_kind=document_kind)
                .order_by("-version")
                .values_list("version", flat=True)
                .first()
            )
            next_version = (last_version or 0) + 1
            return EmployeeGeneratedDocument.objects.create(
                employee=employee,
                document_kind=document_kind,
                version=next_version,
                status="Draft",
                storage_path=storage_path,
                merge_data=merge_data,
                created_by=created_by,
            )
    except IntegrityError:
        raise DocumentGenerationError(
            f"A document was generated for {employee} ({document_kind}) by someone else at the same "
            f"moment — try again."
        )


def issue_document(document, issued_by):
    if document.status != "Draft":
        raise DocumentLifecycleError(f"Cannot issue — document is {document.status}, not Draft.")

    with transaction.atomic():
        # Demote any existing Issued row for this employee+kind FIRST —
        # the partial unique constraint (one Issued row per employee+kind)
        # would reject promoting `document` below otherwise. A plain
        # queryset.update() is fine here specifically: Superseded has no
        # save()-time side effect being bypassed, unlike the bulk-action
        # lesson this codebase otherwise follows carefully.
        EmployeeGeneratedDocument.objects.filter(
            employee=document.employee, document_kind=document.document_kind, status="Issued",
        ).update(status="Superseded", superseded_at=timezone.now())

        document.status = "Issued"
        document.issued_by = issued_by
        document.issued_at = timezone.now()
        document.save()
    return document


def discard_document(document):
    if document.status != "Draft":
        raise DocumentLifecycleError(f"Cannot discard — document is {document.status}, not Draft.")
    document.delete()


def record_acceptance(document, signature_name):
    if document.status != "Issued":
        raise DocumentLifecycleError(f"Cannot record acceptance — document is {document.status}, not Issued.")
    if not signature_name or not signature_name.strip():
        raise DocumentGenerationError("A signature name is required to record acceptance.")

    document.acceptance_signature_name = signature_name.strip()
    document.accepted_at = timezone.now()
    document.save()
    return document
