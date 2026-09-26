"""Posts real-world events to the general ledger — a Payroll Run
(Merge Phase 2, Session 2) and an Expense (Session 3).
Ports the ERP's post_payroll_run() RPC — specifically the CORRECT,
post-Tier-2-incident-revert version of the scheme: SSNIT has both an
employee and an employer side; Tier 2 has ONLY an employee side, no
employer Tier 2 line anywhere (see docs/DESIGN.md's payroll section for
the full incident writeup — account 5146 "Employer Tier 2 Contribution"
does not exist and must never be created, confirmed 2026-09-26).

This module deliberately imports modules.hr.models (PayrollRun) — a
real, intentional coupling, unlike the "don't cross-import between
module apps" discipline that governs truly generic shared code
(tcs_os.text_merge, tcs_os.reference_counter). Bridging hr's payroll
output into finance's ledger is this session's whole purpose, not
incidental coupling — the same way finance.models already imports
modules.admissions.models.Campus. The dependency runs one way only:
modules.hr never imports anything from modules.finance (views.py calls
into this module, not the reverse), so there is no circular import.

ONE aggregated JournalEntry per run (not one per payslip), matching the
ERP's own post_day_close_journal_entry()-style aggregation. Only
callable when payroll_run.status == "ready_for_review"; the
select_for_update() re-check inside the transaction is the same
fresh-DB-read discipline Payslip._payroll_run_is_posted() already
established in hr Session 6, closing the same class of cross-request
race rather than trusting an in-memory status.
"""

import calendar
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from modules.hr.models import PayrollRun

from .models import Account, ExpenseCategoryAccount, JournalEntry, JournalLine

# Payment method -> the asset account post_expense() credits. Looked up
# once, not hardcoded as magic strings through the line-building logic —
# same discipline as ACCOUNT_CODES below.
EXPENSE_PAYMENT_METHOD_ACCOUNT_CODES = {
    "Cash": "1000",          # Cash on Hand
    "Bank": "1010",           # Cash in Bank
    "Mobile Money": "1020",   # Mobile Money Float
}

# code -> what it means in this entry. Looked up once per call (not
# hardcoded as magic strings scattered through the line-building logic
# below) — see _get_accounts().
ACCOUNT_CODES = {
    "salaries_expense": "5140",       # Dr — gross pay
    "salaries_payable": "2300",       # Cr — net pay, accrued not yet disbursed
    "ssnit_payable": "2310",          # Cr — both employee- and employer-side SSNIT withheld/owed
    "employer_ssnit_expense": "5145",  # Dr — employer's 13% SSNIT cost
    "tier2_payable": "2320",          # Cr — employee-side Tier 2 withheld (no employer side exists)
    "paye_payable": "2330",           # Cr — PAYE withheld
    "staff_advances": "1350",         # Cr — IOU repayment reduces this asset
    "staff_fines_recovered": "4910",  # Cr — fines deducted from pay
}


class PostingError(Exception):
    """Raised when a PayrollRun can't be posted as requested — wrong
    status (not Ready for Review, or already Posted), no payslips on
    the run, or a required chart-of-accounts row is missing. Never
    silently no-ops or posts a partial/wrong entry."""


def _get_accounts():
    accounts = Account.objects.in_bulk(ACCOUNT_CODES.values(), field_name="code")
    missing = [code for code in ACCOUNT_CODES.values() if code not in accounts]
    if missing:
        raise PostingError(
            f"Missing required chart-of-accounts row(s): {', '.join(missing)}. "
            f"Seed migration 0002 should have created these — check the finance app's migrations ran."
        )
    return {key: accounts[code] for key, code in ACCOUNT_CODES.items()}


def _build_lines(aggregates, accounts):
    """Returns a list of (account, debit, credit, description) tuples,
    omitting any line whose amount would be zero — a zero-amount line
    would fail JournalLine's own "not both zero" CHECK constraint at the
    DB level regardless, but skip it up front rather than attempting
    (and relying on) that insert failure."""
    lines = [
        (accounts["salaries_expense"], aggregates["gross"], Decimal("0"), "Gross pay"),
        (accounts["salaries_payable"], Decimal("0"), aggregates["net"], "Net pay payable"),
    ]

    if aggregates["ssnit_employee"] > 0:
        lines.append((accounts["ssnit_payable"], Decimal("0"), aggregates["ssnit_employee"], "SSNIT withheld (employee)"))

    if aggregates["ssnit_employer"] > 0:
        # A self-balancing pair on top of the main entry, on the SAME
        # 2310 account as the employee-side credit above — matches the
        # ERP's exact design: 2310 carries both the withheld employee
        # portion and the employer's own cost, from opposite sides.
        lines.append((accounts["employer_ssnit_expense"], aggregates["ssnit_employer"], Decimal("0"), "Employer SSNIT contribution"))
        lines.append((accounts["ssnit_payable"], Decimal("0"), aggregates["ssnit_employer"], "Employer SSNIT contribution"))

    if aggregates["tier2"] > 0:
        lines.append((accounts["tier2_payable"], Decimal("0"), aggregates["tier2"], "Tier 2 withheld"))

    if aggregates["paye"] > 0:
        lines.append((accounts["paye_payable"], Decimal("0"), aggregates["paye"], "PAYE withheld"))

    if aggregates["iou"] > 0:
        # A credit here reduces the Advances to Staff asset — repayment,
        # not a new advance. Matches the ERP's own comment on this line.
        lines.append((accounts["staff_advances"], Decimal("0"), aggregates["iou"], "Staff advance recovery"))

    if aggregates["fines"] > 0:
        lines.append((accounts["staff_fines_recovered"], Decimal("0"), aggregates["fines"], "Staff fines recovered"))

    return lines


def post_payroll_run(payroll_run, created_by):
    """Aggregates every Payslip on payroll_run into ONE JournalEntry (not
    one per payslip), flips the run to Posted, and returns the entry.
    Raises PostingError — never silently no-ops — if the run isn't
    Ready for Review, has no payslips, or a required account is missing.
    """
    if payroll_run.status != "ready_for_review":
        raise PostingError(
            f'Cannot post "{payroll_run}" — status is {payroll_run.get_status_display()}, not Ready for Review.'
        )

    aggregates = payroll_run.payslips.aggregate(
        gross=Sum("gross_salary"), net=Sum("net_pay"),
        ssnit_employee=Sum("ssnit"), ssnit_employer=Sum("ssnit_employer"),
        tier2=Sum("tier2"), paye=Sum("paye"), fines=Sum("fines"), iou=Sum("iou"),
    )
    if aggregates["gross"] is None:
        raise PostingError(f'"{payroll_run}" has no payslips to post.')

    accounts = _get_accounts()
    lines = _build_lines(aggregates, accounts)

    total_debit = sum(line[1] for line in lines)
    total_credit = sum(line[2] for line in lines)
    # Balanced by construction, but confirmed rather than trusted — see
    # module docstring and CLAUDE.md's "verify, don't just trust the
    # arithmetic" discipline. A raised exception, not a bare `assert` —
    # `assert` is stripped entirely under `python -O`, which would
    # silently drop this financial-integrity guarantee exactly where it
    # matters most; a real exception can't vanish that way.
    if total_debit != total_credit:
        raise PostingError(
            f'"{payroll_run}" journal entry does not balance: debit {total_debit} != credit {total_credit}'
        )

    last_day = calendar.monthrange(payroll_run.year, payroll_run.month)[1]
    entry_date = date(payroll_run.year, payroll_run.month, last_day)
    description = f"Payroll — {calendar.month_name[payroll_run.month]} {payroll_run.year}"

    with transaction.atomic():
        # Fresh, locked re-read of the run's own status — closes the
        # same cross-request race Payslip._payroll_run_is_posted() was
        # fixed to close in hr Session 6 (a second concurrent approval
        # attempt must never create a second entry for an already-Posted
        # run), rather than trusting the payroll_run object the caller
        # already had in memory.
        locked_run = PayrollRun.objects.select_for_update().get(pk=payroll_run.pk)
        if locked_run.status != "ready_for_review":
            raise PostingError(
                f'Cannot post "{locked_run}" — status is {locked_run.get_status_display()}, '
                f"not Ready for Review (posted by someone else already)."
            )

        entry = JournalEntry.objects.create(
            campus=locked_run.branch, entry_date=entry_date, description=description, created_by=created_by,
        )
        for position, (account, debit, credit, line_description) in enumerate(lines):
            JournalLine.objects.create(
                entry=entry, position=position, account=account, debit=debit, credit=credit,
                description=line_description,
            )

        locked_run.status = "posted"
        locked_run.save()

    return entry


def post_expense(expense, created_by):
    """Auto-posts a single Expense as a TWO-line JournalEntry (Session 3):
        Dr <the expense's category's mapped account>   expense.amount
        Cr <the asset account for expense.method>       expense.amount
    Balanced by construction (one debit, one credit, same amount) — still
    explicitly confirmed below rather than just assumed, same discipline
    as post_payroll_run().

    Deliberately called explicitly from the expense-creation VIEW right
    after Expense.objects.create(), not from a signal or an Expense.save()
    override — there is no signal-based or save()-override precedent
    anywhere in this codebase for a creation-time cross-app side effect;
    the one real precedent (post_payroll_run(), Session 2) is itself
    called explicitly from PayrollRunApproveView, not from PayrollRun.save().
    This function follows that same shape.

    Idempotent, not error-on-repeat: unlike post_payroll_run() (a
    deliberate, repeatable user action worth a clear rejection on
    re-attempt), this is meant to fire once as an automatic side effect of
    creating an Expense — a second call (a retry, a duplicate view
    invocation) returns the SAME JournalEntry rather than raising, via
    JournalEntry.expense's OneToOneField uniqueness: an application-level
    pre-check handles the common case, and the field's own DB-level
    uniqueness (the real guarantee, not just the pre-check) is what a
    genuine race falls back on — caught as IntegrityError and resolved to
    the entry that actually won, never left to surface as a raw 500 or,
    worse, silently create two entries for one expense."""
    existing = JournalEntry.objects.filter(expense=expense).first()
    if existing is not None:
        return existing

    try:
        mapping = expense.category.account_mapping
    except ExpenseCategoryAccount.DoesNotExist:
        raise PostingError(
            f'"{expense.category}" has no ledger account mapping — map it via '
            f"ExpenseCategoryAccount before this expense can be posted."
        )
    expense_account = mapping.account

    asset_code = EXPENSE_PAYMENT_METHOD_ACCOUNT_CODES.get(expense.method)
    if asset_code is None:
        raise PostingError(f"Unrecognized payment method {expense.method!r} — cannot determine which asset account to credit.")
    asset_account = Account.objects.filter(code=asset_code).first()
    if asset_account is None:
        raise PostingError(f"Missing chart-of-accounts row for code {asset_code!r} (payment method {expense.method!r}).")

    if expense_account.normal_balance != "debit":
        # Sanity check, not a real-world expectation to fail — every
        # seeded Expenses-category account is debit-normal by definition
        # (see Account.DEBIT_NORMAL_CATEGORIES), but a category mapped to
        # the wrong kind of account would silently misstate the ledger
        # otherwise, so this is confirmed rather than assumed.
        raise PostingError(
            f'"{expense_account}" is not a debit-normal account — cannot use it as an expense '
            f"category's ledger account."
        )

    try:
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                campus=expense.campus, entry_date=expense.date,
                description=f"Expense — {expense.id}: {expense.description}",
                expense=expense, created_by=created_by,
            )
            JournalLine.objects.create(
                entry=entry, position=0, account=expense_account,
                debit=expense.amount, credit=Decimal("0"), description=expense.description,
            )
            JournalLine.objects.create(
                entry=entry, position=1, account=asset_account,
                debit=Decimal("0"), credit=expense.amount, description=expense.description,
            )
    except IntegrityError:
        # Someone else's concurrent post_expense() call for this same
        # expense won the race — return what actually got created, don't
        # duplicate and don't crash.
        return JournalEntry.objects.get(expense=expense)

    return entry
