"""Session 3 — accounting views: chart of accounts (list/create/edit),
expense entry + auto-posting, expense list, and a read-only journal
entries list/detail (showing entries from both post_payroll_run() and
post_expense()). Staff-facing, server-rendered HTML pages, same shape as
hr's views.py (Session 6): plain Django class-based views, no DRF, using
the shared tcs_os.staff_views.StaffRequiredMixin for auth.

Gated on finance.can_manage_accounts (chart of accounts) or
finance.can_record_expenses (expenses) — see models.py's Meta.permissions
and migrations/0004_accounting_permissions.py. Journal entries are
viewable by either permission, the same "any of two permissions" pattern
hr's PayrollRunDetailView established in Session 6.
"""

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from modules.admissions.models import Campus
from tcs_os.staff_views import StaffRequiredMixin

from . import storage
from .models import Account, Expense, ExpenseCategory, JournalEntry
from .posting import PostingError, post_expense


class FinanceStaffRequiredMixin(StaffRequiredMixin):
    """See tcs_os.staff_views.StaffRequiredMixin — same shared mixin
    hr.views.HrStaffRequiredMixin now also subclasses (extracted here in
    this session)."""


class AccountListView(FinanceStaffRequiredMixin, View):
    permission_required = "finance.can_manage_accounts"

    def get(self, request):
        accounts = Account.objects.all()
        category = request.GET.get("category")
        if category:
            accounts = accounts.filter(category=category)
        return render(
            request, "finance/account_list.html",
            {"accounts": accounts, "categories": Account.CATEGORY_CHOICES, "selected_category": category},
        )


def _validate_account_fields(data):
    errors = {}
    code = (data.get("code") or "").strip()
    name = (data.get("name") or "").strip()
    category = data.get("category") or ""
    subtype = (data.get("subtype") or "").strip()
    description = (data.get("description") or "").strip()

    if not re.match(r"^[0-9]{3,6}$", code):
        errors["code"] = "Account code must be 3-6 digits."
    if not name:
        errors["name"] = "Name is required."
    if category not in dict(Account.CATEGORY_CHOICES):
        errors["category"] = "Select a valid category."
    if not subtype:
        errors["subtype"] = "Subtype is required."

    return errors, {
        "code": code, "name": name, "category": category, "subtype": subtype, "description": description,
    }


class AccountCreateView(FinanceStaffRequiredMixin, View):
    permission_required = "finance.can_manage_accounts"

    def get(self, request):
        return render(
            request, "finance/account_form.html",
            {"errors": {}, "values": {}, "categories": Account.CATEGORY_CHOICES, "is_edit": False},
        )

    def post(self, request):
        errors, values = _validate_account_fields(request.POST)

        if not errors and Account.objects.filter(code=values["code"]).exists():
            errors["code"] = f'An account with code {values["code"]} already exists.'

        if errors:
            return render(
                request, "finance/account_form.html",
                {"errors": errors, "values": values, "categories": Account.CATEGORY_CHOICES, "is_edit": False},
            )

        try:
            account = Account.objects.create(created_by=request.user, **values)
        except IntegrityError:
            errors["code"] = f'An account with code {values["code"]} already exists.'
            return render(
                request, "finance/account_form.html",
                {"errors": errors, "values": values, "categories": Account.CATEGORY_CHOICES, "is_edit": False},
            )

        return redirect("finance:account-list")


class AccountUpdateView(FinanceStaffRequiredMixin, View):
    """Editing is the only way to change an Account — including
    is_active, so "deactivate" is just an edit that unchecks it. No
    delete route exists at all."""

    permission_required = "finance.can_manage_accounts"

    def get(self, request, pk):
        account = get_object_or_404(Account, pk=pk)
        return render(
            request, "finance/account_form.html",
            {
                "errors": {}, "is_edit": True, "account": account, "categories": Account.CATEGORY_CHOICES,
                "values": {
                    "code": account.code, "name": account.name, "category": account.category,
                    "subtype": account.subtype, "description": account.description,
                },
                "is_active": account.is_active,
            },
        )

    def post(self, request, pk):
        account = get_object_or_404(Account, pk=pk)
        errors, values = _validate_account_fields(request.POST)
        is_active = bool(request.POST.get("is_active"))

        if not errors and Account.objects.filter(code=values["code"]).exclude(pk=account.pk).exists():
            errors["code"] = f'Another account already uses code {values["code"]}.'

        if errors:
            return render(
                request, "finance/account_form.html",
                {
                    "errors": errors, "is_edit": True, "account": account, "categories": Account.CATEGORY_CHOICES,
                    "values": values, "is_active": is_active,
                },
            )

        account.code = values["code"]
        account.name = values["name"]
        account.category = values["category"]
        account.subtype = values["subtype"]
        account.description = values["description"]
        account.is_active = is_active
        account.save()

        return redirect("finance:account-list")


class ExpenseReceiptUploadURLView(FinanceStaffRequiredMixin, View):
    """Mints a signed upload URL for a receipt file — same two-step
    handshake as admissions' UploadURLView, called via JS fetch from
    expense_form.html before the expense form itself is submitted.

    Extension + client-declared file_size are validated here, mirroring
    admissions' UploadURLRequestSerializer exactly — layer 2 of the
    three-layer upload-validation convention (docs/CONSTRAINTS.md):
    client (the <input accept=...>, trivially bypassed), this layer
    (rejects a bad request before a signed URL is even minted, but
    trusts the client-declared file_size), and the finance-receipts
    bucket's own file_size_limit/allowed_mime_types (layer 3, the only
    one that checks the real bytes on the actual PUT — see
    configure_bucket_limits() below and manage.py
    configure_finance_storage_bucket)."""

    permission_required = "finance.can_record_expenses"

    def post(self, request):
        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"detail": "Invalid request."}, status=400)

        filename = (body.get("filename") or "").strip()
        if not filename:
            return JsonResponse({"detail": "filename is required."}, status=400)

        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in storage.ALLOWED_UPLOAD_EXTENSIONS:
            allowed = ", ".join(sorted(storage.ALLOWED_UPLOAD_EXTENSIONS))
            return JsonResponse({"detail": f"Unsupported file type. Allowed types: {allowed}."}, status=400)

        try:
            file_size = int(body.get("file_size") or 0)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "file_size is required."}, status=400)
        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if file_size <= 0 or file_size > max_bytes:
            return JsonResponse(
                {"detail": f"File is too large. Maximum size is {settings.MAX_UPLOAD_SIZE_MB}MB."}, status=400,
            )

        try:
            storage_path, upload_url = storage.create_upload_target(filename)
        except storage.FinanceStorageError:
            return JsonResponse({"detail": "Could not prepare the file upload. Please try again."}, status=502)

        return JsonResponse({"upload_url": upload_url, "storage_path": storage_path})


class ExpenseCreateView(FinanceStaffRequiredMixin, View):
    """On successful save, calls post_expense() immediately (the
    established explicit-call-from-the-view pattern, not a signal or an
    Expense.save() override — see posting.post_expense()'s own
    docstring) and shows the resulting journal entry's id on the
    confirmation page, so a user sees the posting actually happened."""

    permission_required = "finance.can_record_expenses"

    def get(self, request):
        return render(
            request, "finance/expense_form.html",
            {"errors": {}, "values": {}, "categories": ExpenseCategory.objects.all(), "campuses": Campus.objects.all()},
        )

    def post(self, request):

        errors = {}
        campus = Campus.objects.filter(pk=request.POST.get("campus")).first()
        if campus is None:
            errors["campus"] = "Campus is required."

        category = ExpenseCategory.objects.filter(pk=request.POST.get("category")).first()
        if category is None:
            errors["category"] = "Category is required."
        elif campus is not None and category.campus_id != campus.pk:
            # Campus and category are independent form fields — nothing
            # about the model layer itself stops submitting mismatched
            # ones (e.g. campus=Main with a category that only exists
            # for Annex). Caught here explicitly rather than trusting the
            # dropdown's own labelling to keep the two in sync.
            errors["category"] = f'"{category}" belongs to a different campus than the one selected.'

        date_str = request.POST.get("date") or ""
        expense_date = None
        try:
            expense_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            errors["date"] = "A valid date is required."

        description = (request.POST.get("description") or "").strip()
        if not description:
            errors["description"] = "Description is required."

        try:
            amount = Decimal(request.POST.get("amount") or "")
            if amount <= 0:
                errors["amount"] = "Amount must be greater than zero."
        except (InvalidOperation, TypeError):
            errors["amount"] = "A valid amount is required."
            amount = None

        method = request.POST.get("method") or ""
        if method not in dict(Expense.METHOD_CHOICES):
            errors["method"] = "Select a valid payment method."

        reference = (request.POST.get("reference") or "").strip()
        receipt_path = (request.POST.get("receipt_path") or "").strip()

        values = {
            "campus": request.POST.get("campus", ""), "category": request.POST.get("category", ""),
            "date": date_str, "description": description, "amount": request.POST.get("amount", ""),
            "method": method, "reference": reference,
        }

        if errors:
            return render(
                request, "finance/expense_form.html",
                {
                    "errors": errors, "values": values, "categories": ExpenseCategory.objects.all(),
                    "campuses": Campus.objects.all(),
                },
            )

        expense = Expense.objects.create(
            campus=campus, date=expense_date, category=category, description=description,
            amount=amount, method=method, reference=reference, receipt_path=receipt_path,
            recorded_by=request.user,
        )

        try:
            entry = post_expense(expense, created_by=request.user)
        except PostingError as exc:
            return render(
                request, "finance/expense_post_result.html",
                {"expense": expense, "error": str(exc)},
            )

        return render(
            request, "finance/expense_post_result.html",
            {"expense": expense, "entry": entry},
        )


class ExpenseListView(FinanceStaffRequiredMixin, View):
    permission_required = "finance.can_record_expenses"

    def get(self, request):
        expenses = Expense.objects.select_related("campus", "category")

        campus_id = request.GET.get("campus")
        if campus_id:
            expenses = expenses.filter(campus_id=campus_id)

        category_id = request.GET.get("category")
        if category_id:
            expenses = expenses.filter(category_id=category_id)

        date_from = request.GET.get("date_from")
        if date_from:
            expenses = expenses.filter(date__gte=date_from)

        date_to = request.GET.get("date_to")
        if date_to:
            expenses = expenses.filter(date__lte=date_to)


        return render(
            request, "finance/expense_list.html",
            {
                "expenses": expenses, "campuses": Campus.objects.all(), "categories": ExpenseCategory.objects.all(),
                "filters": {
                    "campus": campus_id or "", "category": category_id or "",
                    "date_from": date_from or "", "date_to": date_to or "",
                },
            },
        )


class JournalEntryListView(FinanceStaffRequiredMixin, View):
    """Viewable by either finance permission — an accounts manager and
    an expense recorder both have a real reason to see the ledger,
    matching hr's PayrollRunDetailView "any of two permissions"
    precedent from Session 6."""

    permission_required = ("finance.can_manage_accounts", "finance.can_record_expenses")

    def has_permission(self):
        return any(self.request.user.has_perm(perm) for perm in self.get_permission_required())

    def get(self, request):
        entries = JournalEntry.objects.select_related("campus").order_by("-entry_date", "-created_at")
        return render(request, "finance/journal_entry_list.html", {"entries": entries})


class JournalEntryDetailView(FinanceStaffRequiredMixin, View):
    permission_required = ("finance.can_manage_accounts", "finance.can_record_expenses")

    def has_permission(self):
        return any(self.request.user.has_perm(perm) for perm in self.get_permission_required())

    def get(self, request, pk):
        entry = get_object_or_404(JournalEntry, pk=pk)
        return render(
            request, "finance/journal_entry_detail.html",
            {"entry": entry, "lines": entry.lines.select_related("account").all()},
        )
