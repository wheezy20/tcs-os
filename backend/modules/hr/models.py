"""HR & Payroll module — a port of the TCS ERP's payroll logic (SSNIT,
Tier 2, graduated PAYE) onto Django. See docs/hr/ once that lands; for now
the source of truth is this file's docstrings plus hr/payroll.py.

Money fields are all DecimalField (never float — floats lose cents over
enough arithmetic, unacceptable for payroll). Percentages (PAYEBand.rate,
StatutoryRate.*_pct) are stored as decimal FRACTIONS (0.0500 = 5%), not
whole-number percentages — see each field's help_text. This mirrors how the
ERP's calculation reads them and avoids a classic "is this 13 or 0.13" bug
at the one place it matters most.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Employee(models.Model):
    """A person on TCS's payroll. `basic_salary` here is a convenience/
    display value (e.g. what shows on the employee's own record) — it is
    NOT what payroll actually calculates against. The authoritative,
    period-effective, approval-gated salary is EmployeePayConfig; see that
    model's docstring and hr/payroll.py's _get_effective_pay_config(). The
    two can legitimately disagree for a while (a raise is agreed but not
    yet approved/effective)."""

    EMPLOYMENT_STATUS_CHOICES = [
        ("active", "Active"),
        ("on_leave", "On Leave"),
        ("suspended", "Suspended"),
        ("terminated", "Terminated"),
    ]

    employee_number = models.CharField(
        max_length=30, unique=True,
        help_text="The ERP's own staff ID — kept as the stable external reference across systems.",
    )
    first_name = models.CharField(max_length=255)
    surname = models.CharField(max_length=255)
    basic_salary = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Display/reference only — see the model docstring. Payroll uses EmployeePayConfig.",
    )
    employment_status = models.CharField(
        max_length=20, choices=EMPLOYMENT_STATUS_CHOICES, default="active",
    )

    # Session 7 — reference/merge-field data for generated documents
    # (documents.py). Plain free-text fields, deliberately no FK to a
    # position/department reference table yet (future scope, same
    # deferral noted for these two back in Session 4's docs). No choices
    # on employment_type/payment_method either — no existing reference
    # list to draw an enum from, and inconsistent free text (e.g.
    # "Full-time" vs "Full Time") is an accepted tradeoff for now; worth
    # revisiting once real data shows this needs tightening.
    position = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    employment_type = models.CharField(max_length=50, blank=True)
    payment_method = models.CharField(max_length=50, blank=True)
    start_date = models.DateField(
        null=True, blank=True,
        help_text="Also used to compute probation_duration_months for a Probation Letter, "
        "alongside probation_end_date — see documents.build_merge_data().",
    )
    probation_end_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["surname", "first_name"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.surname}".strip()

    def __str__(self):
        return f"{self.full_name} ({self.employee_number})"


class EmployeePayConfig(models.Model):
    """The authoritative, time-versioned payroll configuration for an
    Employee — what hr/payroll.py's calculate_payslip() actually reads.
    Versioned (effective_from/effective_to) rather than mutated in place, so
    a payslip run for a past month keeps calculating against what was
    actually approved and in force *that month*, even after a later raise
    changes the current config. `effective_to = None` means "current, open-
    ended" — there should be at most one such row per employee, but that's
    a data-hygiene expectation, not a DB constraint yet (see the model's
    clean()).

    Requires `approval_status = "approved"` before payroll will use it —
    calculate_payslip() deliberately raises rather than silently falling
    back to an unapproved or a stale config; see PayrollConfigError."""

    APPROVAL_STATUS_CHOICES = [
        ("pending", "Pending Approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="pay_configs")
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    pays_ssnit = models.BooleanField(default=True)
    pays_tier2 = models.BooleanField(default=True)
    pays_paye = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(
        null=True, blank=True, help_text="Blank = current, open-ended.",
    )
    approval_status = models.CharField(
        max_length=10, choices=APPROVAL_STATUS_CHOICES, default="pending",
    )

    class Meta:
        ordering = ["-effective_from"]

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "Must be on or after effective_from."})

    def __str__(self):
        to = self.effective_to.isoformat() if self.effective_to else "open"
        return f"{self.employee} pay config {self.effective_from.isoformat()}→{to} ({self.get_approval_status_display()})"


class PayrollRun(models.Model):
    """One payroll cycle for one branch/month/year. `branch` FKs to
    admissions.Campus (Merge Phase 2 Session 2) — the same "reference
    the module that owns the entity" pattern already used for
    finance.Expense/JournalEntry/ExpenseCategory, rather than the plain
    CharField this field started as in Session 1. Migrated with a clean
    schema change, not a data migration: no real payroll has ever run
    (the ERP itself has only run test payroll), so there was no string
    branch data worth preserving/converting. PROTECT: a Campus shouldn't
    vanish out from under a PayrollRun that references it.

    Session 6 — the two custom permissions below gate the payroll workflow
    views (modules/hr/views.py): can_process_payroll (create a run,
    generate payslips, submit for review — the "Payroll Processor" Group)
    and can_approve_payroll (approve & post, reject — granted only to the
    existing admissions "Administration" Group, deliberately not
    delegable to a processor role; see migrations/0003). Once status is
    "posted", the run's payslips are immutable — see Payslip.save()/
    delete() below, the model-level backstop that holds regardless of
    which view/admin/shell path is used, following the same pattern as
    Application.save()'s stage gate in modules/admissions/models.py."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("ready_for_review", "Ready for Review"),
        ("posted", "Posted"),
    ]

    branch = models.ForeignKey(
        "admissions.Campus", on_delete=models.PROTECT, related_name="payroll_runs",
    )
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    year = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    rejection_reason = models.TextField(
        blank=True,
        help_text="Required when rejecting a run back to Draft from Ready for Review — see "
        "PayrollRunRejectView. Not cleared automatically on the next submit, so a reviewer "
        "can still see what was fixed; overwritten by the next rejection, if any.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month", "branch"]
        unique_together = ("branch", "month", "year")
        permissions = [
            ("can_process_payroll", "Can create payroll runs, generate payslips, and submit for review"),
            ("can_approve_payroll", "Can approve, post, and reject payroll runs"),
        ]

    def __str__(self):
        return f"{self.branch} {self.month:02d}/{self.year} ({self.get_status_display()})"


class PayrollRunLockedError(Exception):
    """Raised by Payslip.save()/delete() when the parent PayrollRun is
    already "posted" — see Payslip's docstring. Mirrors PayrollConfigError
    (payroll.py) as a plain, deliberate exception rather than a silent
    no-op: once a run is posted, no further create/update/delete on its
    payslips is allowed, matching the admin-layer lockdown already in
    place (PayslipAdmin disables add/change/delete outright) and the same
    "once closed, no edits to history" convention Application.save()'s
    stage gate follows in modules/admissions/models.py."""


class Payslip(models.Model):
    """One employee's calculated result for one PayrollRun — the persisted
    form of whatever hr/payroll.py's calculate_payslip() returned.
    Field-for-field mirror of that function's return dict (see its
    docstring) so saving one is just `Payslip.objects.create(payroll_run=...,
    employee=..., **calculate_payslip(...))`. Itemized allowance lines
    aren't persisted here yet — only the summed total_allowances — since no
    per-line allowance model was in scope for this port.

    Immutable once its PayrollRun is posted (Session 6) — see save()/
    delete() below. This is a model-level backstop, not just a view-layer
    permission check or the existing admin-layer lockdown (PayslipAdmin):
    it holds even against a shell session, a future view that forgets to
    check run.status, or any other code path that isn't the intended
    payroll workflow."""

    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")

    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    overtime_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_allowances = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)

    ssnit = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="The employee's 0.5% SSNIT Tier 1 share — deducted from net pay.",
    )
    tier2 = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="The employee's 5% Tier 2 share (100% employee-funded) — deducted from net pay.",
    )
    paye = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    ssnit_employer = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text="Employer-side Tier 1 SSNIT cost (13%). Not a deduction from the employee — "
        "excluded from total_deductions/net_pay, tracked here purely as an employer cost record. "
        "There is no employer-side Tier 2 field — Tier 2 has no employer contribution.",
    )
    fines = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    iou = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payroll_run__year", "-payroll_run__month", "employee"]
        unique_together = ("payroll_run", "employee")

    def __str__(self):
        return f"Payslip: {self.employee} — {self.payroll_run}"

    def _payroll_run_is_posted(self):
        """Deliberately a fresh DB read (self.payroll_run_id, not
        self.payroll_run.status) — the FK's cached related object can be
        stale if `self.payroll_run` was fetched before another request
        posted the run, which would let a real concurrent-request race
        slip past a check against the in-memory attribute. This closes
        that: every save()/delete() re-checks the run's CURRENT status in
        the database, not whatever status the caller's Python object
        happened to hold when it was first loaded."""
        return PayrollRun.objects.filter(pk=self.payroll_run_id).values_list("status", flat=True).first() == "posted"

    def save(self, *args, **kwargs):
        if self._payroll_run_is_posted():
            raise PayrollRunLockedError(
                f"{self.payroll_run} is posted — its payslips are locked. "
                f"A correction requires reopening the run, not editing a posted payslip."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._payroll_run_is_posted():
            raise PayrollRunLockedError(
                f"{self.payroll_run} is posted — its payslips are locked and cannot be deleted."
            )
        super().delete(*args, **kwargs)


class AllowanceType(models.Model):
    """A category of allowance (e.g. Housing, Transport, Fuel). `taxable`
    decides whether an allowance of this type counts toward the PAYE base
    in hr/payroll.py's calculate_payslip() — it always counts toward gross
    pay either way; taxability only affects what PAYE is calculated on."""

    name = models.CharField(max_length=100, unique=True)
    taxable = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({'taxable' if self.taxable else 'non-taxable'})"


class PAYEBand(models.Model):
    """One bracket of the graduated PAYE table, as a cumulative income
    range: `lower_bound`/`upper_bound` are absolute taxable-income
    thresholds (not the "width" of the band), so the marginal rate for
    income between `lower_bound` and `upper_bound` is `rate`.
    `upper_bound = None` marks the final, unbounded top band.

    A full PAYE table is versioned as one atomic set sharing the same
    `effective_from` (GRA republishes the whole table when it changes, not
    one band at a time) — `band_order` fixes the sequence within that set.
    See hr/payroll.py's _get_effective_paye_bands(), which resolves "the
    current table" by finding the latest effective_from on/before the
    payroll period and taking every band that shares it."""

    band_order = models.PositiveSmallIntegerField()
    lower_bound = models.DecimalField(max_digits=12, decimal_places=2)
    upper_bound = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Blank = unbounded (the top band).",
    )
    rate = models.DecimalField(
        max_digits=5, decimal_places=4,
        help_text="Decimal fraction, e.g. 0.0500 for 5% — not a whole-number percentage.",
    )
    effective_from = models.DateField()

    class Meta:
        ordering = ["-effective_from", "band_order"]
        unique_together = ("effective_from", "band_order")

    def __str__(self):
        upper = self.upper_bound if self.upper_bound is not None else "∞"
        return f"Band {self.band_order}: {self.lower_bound}–{upper} @ {self.rate} (from {self.effective_from.isoformat()})"


class StatutoryRate(models.Model):
    """SSNIT (Tier 1) / Tier 2 rates in force from a given date — versioned
    the same way as PAYEBand (statutory rates change rarely but do change).
    See hr/payroll.py's _get_effective_statutory_rate(): the row with the
    latest effective_from on/before the payroll period wins.

    Corrected 2026-09-23 to the actual Ghana scheme under the National
    Pensions Act, Act 766 (an earlier pass on this same date had this
    wrong — see git history for the reverted attempt). 18.5% of basic is
    mandatory in total, split:
      - Employee pays 5.5% total, BOTH deducted from net pay:
          0.5% -> SSNIT Tier 1 (ssnit_employee_pct)
          5%   -> Tier 2, private trustee            (tier2_employee_pct)
      - Employer pays 13% total, entirely to Tier 1, employer cost only,
        never deducted from net pay (ssnit_employer_pct).
      - There is NO employer Tier 2 contribution — Tier 2 is 100%
        employee-funded. Deliberately no tier2_employer_pct field.
    Tier 1 total = 13.5% (13% employer + 0.5% employee). Tier 2 total = 5%
    (all employee). See calculate_payslip()'s docstring in hr/payroll.py
    for the full breakdown."""

    ssnit_employee_pct = models.DecimalField(
        max_digits=5, decimal_places=4,
        help_text="SSNIT Tier 1 — the employee's share, e.g. 0.0050 for 0.5%. Deducted from net pay.",
    )
    ssnit_employer_pct = models.DecimalField(
        max_digits=5, decimal_places=4,
        help_text="SSNIT Tier 1 — employer-only, e.g. 0.1300 for 13%. Never touches net pay.",
    )
    tier2_employee_pct = models.DecimalField(
        max_digits=5, decimal_places=4,
        help_text="Tier 2 (private trustee) — 100% employee-funded, e.g. 0.0500 for 5%. "
        "Deducted from net pay. There is no employer Tier 2 contribution.",
    )
    effective_from = models.DateField(unique=True)

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self):
        return (
            f"Statutory rates from {self.effective_from.isoformat()}: "
            f"SSNIT (Tier 1) employee {self.ssnit_employee_pct} / employer {self.ssnit_employer_pct}; "
            f"Tier 2 (employee-only) {self.tier2_employee_pct}"
        )


# Session 7 — employee-generated documents. One generalized lifecycle
# table with a document_kind column (see EmployeeGeneratedDocument),
# porting the ERP's employee_generated_documents design rather than one
# table per document type — see docs/DESIGN.md's "Employee-generated
# documents" section. Shared by both ContractTemplate.category and
# EmployeeGeneratedDocument.document_kind so the two can never drift
# apart into two different sets of "valid kinds".
DOCUMENT_KIND_CHOICES = [
    ("Appointment Letter", "Appointment Letter"),
    ("Probation Letter", "Probation Letter"),
    ("Contract-Teaching", "Contract-Teaching"),
    ("Contract-Non-Teaching", "Contract-Non-Teaching"),
]


class ContractTemplate(models.Model):
    """One editable HTML body per document kind — what documents.py's
    generate_document() merges employee/letterhead data into before
    rendering a PDF. `html_body` uses the {{field}} whitelist-substitution
    convention (tcs_os.text_merge.render_template), never Django's real
    template engine, so an HR staff member editing this in admin can't
    accidentally (or deliberately) execute template logic.

    Seeded with html_body="" for all 4 kinds by an idempotent data
    migration — no letter/contract prose is invented by this port; real
    text has to be entered by a human before a kind is usable. This is
    enforced in documents.generate_document() (raises on blank
    html_body), not just documented here — a blank body must never
    silently produce an empty "document"."""

    category = models.CharField(max_length=30, choices=DOCUMENT_KIND_CHOICES, unique=True)
    html_body = models.TextField(
        blank=True,
        help_text="{{field}} placeholders only — see documents.build_merge_data() for the exact "
        "field list per document kind. Blank means this kind cannot be generated yet.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
    )
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category"]

    def __str__(self):
        return self.category


class EmployeeGeneratedDocument(models.Model):
    """One generated document (a specific PDF, in Supabase Storage) for one
    employee. Draft -> Issued -> Superseded, versioned rather than
    overwritten — old versions are archived (status becomes Superseded),
    never deleted, matching how Payslip/Application treat their own
    finalized records. See documents.py for the three lifecycle
    transitions (issue_document, discard_document, record_acceptance).

    At most one currently-Issued row per (employee, document_kind) is
    enforced at the DATABASE level via the partial unique constraint
    below, not just in application code — issue_document() must demote
    any existing Issued row to Superseded BEFORE promoting a new one, or
    the constraint rejects the write.

    A second constraint enforces unique (employee, document_kind,
    version) tuples at the database level too — generate_document()
    computes the next version under select_for_update() locking, but
    that only locks rows that already exist (the first-ever generation
    for an employee+kind has nothing to lock), so this constraint is the
    real backstop against two concurrent generate_document() calls
    computing the same "next version" and both trying to use it."""

    STATUS_CHOICES = [
        ("Draft", "Draft"),
        ("Issued", "Issued"),
        ("Superseded", "Superseded"),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="generated_documents")
    document_kind = models.CharField(max_length=30, choices=DOCUMENT_KIND_CHOICES)
    version = models.PositiveIntegerField(help_text="1-based, per (employee, document_kind).")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="Draft")

    storage_path = models.CharField(
        max_length=500,
        help_text="Path within the HR_DOCUMENT_STORAGE_BUCKET Supabase Storage bucket — not a "
        "URL. The bucket is private, so viewing a document means minting a fresh signed URL on "
        "demand (modules/hr/storage.py's create_read_url()) rather than storing one, since a "
        "signed URL created before the file exists would 404, and one stored long-term would "
        "eventually expire silently.",
    )
    merge_data = models.JSONField(
        help_text="A frozen snapshot of every field substituted into this specific PDF at "
        "generation time — kept even if the employee's own record changes later, so this "
        "document's history stays an accurate record of what was actually issued.",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hr_documents_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="hr_documents_issued",
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    acceptance_signature_name = models.CharField(
        max_length=255, blank=True,
        help_text="HR-recorded, not self-service signing — see documents.record_acceptance().",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["employee", "document_kind", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "document_kind"],
                condition=models.Q(status="Issued"),
                name="one_issued_document_per_employee_kind",
            ),
            models.UniqueConstraint(
                fields=["employee", "document_kind", "version"],
                name="unique_document_version_per_employee_kind",
            ),
        ]

    def __str__(self):
        return f"{self.document_kind} v{self.version} — {self.employee} ({self.get_status_display()})"
