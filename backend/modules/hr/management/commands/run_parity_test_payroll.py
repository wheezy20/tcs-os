"""Merge Phase 3, Session 1 — drives a real payroll run for the ERP
parity test employees (see seed_parity_test_employees) through the
actual Session 6 staff-facing views (create -> generate -> submit ->
approve), using Django's test Client rather than a script that calls
calculate_payslip()/PayrollRun.objects.create() directly. The point of
this session is proving the real user-facing workflow produces correct
output, not just the underlying calculation in isolation — Session 5
already covers that in unit tests.

Not idempotent across months: rerunning against the same month/year
after a run has posted will just report "already posted" and read back
the existing payslips rather than erroring — safe to rerun.

Ends by printing the ERP-vs-TCS-OS comparison table required by this
session's spec. Only Emmanuel Ansah has an independently-confirmed ERP
ground truth (docs/DESIGN.md) to compare against; the other three
employees' ERP figures were never pulled from a real payslip anywhere
in this project, so they're printed as "NEEDS CONFIRMATION" rather than
compared against a made-up expected value — a comparison against
invented numbers would prove nothing.

DEV/TEST DATABASE ONLY — REAL, PERMANENT SIDE EFFECTS. Approving the
run (the last step) calls the real finance.posting.post_payroll_run(),
which creates and posts an actual, immutable JournalEntry to the real
finance ledger (see docs/DESIGN.md's payslip/journal-entry immutability
rule) — this is not sandboxed or reversible except by a manual
offsetting entry. This is intentional: proving the *real* Session 6
workflow produces correct output, including its ledger-posting side
effect, is the whole point of this parity check. But it means this
command must never be run against a database that also holds real
financial data — dev/test only. The scripted "parity_test_runner"
user is added to the real "Administration" Group only for the duration
of this command's run (docs/CONSTRAINTS.md: Administration/its
permissions are "never auto-granted" outside a deliberate human
decision) and removed again in a `finally` block before the command
exits, even on error.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.test import Client
from django.urls import reverse

from modules.admissions.models import Campus
from modules.hr.models import Employee, PayrollRun

TEST_MONTH = 9
TEST_YEAR = 2026
TEST_CAMPUS_NAME = "Main"

PARITY_TEST_USERNAME = "parity_test_runner"

# Confirmed against a real ERP payslip screenshot, recorded in
# docs/DESIGN.md's payroll ground-truth section. Every other seeded
# employee has no such confirmed source yet.
CONFIRMED_ERP_GROUND_TRUTH = {
    "ERP-0004": {  # Emmanuel Ansah
        "ssnit": Decimal("32.50"),
        "tier2": Decimal("325.00"),
        "paye": Decimal("1134.13"),
        "net_pay": Decimal("5008.37"),
        "ssnit_employer": Decimal("845.00"),
    },
}


class Command(BaseCommand):
    help = (
        "Runs a real PayrollRun through the Session 6 staff views (create, "
        "generate, submit, approve) for the ERP parity test employees, then "
        "prints a TCS OS vs ERP comparison table. Run "
        "seed_parity_test_employees first."
    )

    def handle(self, *args, **options):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=PARITY_TEST_USERNAME,
            defaults={"is_staff": True},
        )
        if created:
            user.set_unusable_password()
            user.save()
        administration_group = Group.objects.get(name="Administration")
        user.groups.add(administration_group)
        try:
            self._run(user, administration_group)
        finally:
            # Never leave the scripted account holding real
            # payroll-approval permissions — see the module docstring.
            user.groups.remove(administration_group)

    def _run(self, user, administration_group):
        campus = Campus.objects.get(name=TEST_CAMPUS_NAME)
        # Django's test Client defaults SERVER_NAME to "testserver", which
        # isn't in this project's real ALLOWED_HOSTS (localhost/127.0.0.1)
        # — use a real allowed host instead of touching settings.
        client = Client(SERVER_NAME="localhost")
        client.force_login(user)

        run = PayrollRun.objects.filter(branch=campus, month=TEST_MONTH, year=TEST_YEAR).first()
        if run is None:
            response = client.post(
                reverse("hr:payroll-run-create"),
                {"branch": campus.pk, "month": TEST_MONTH, "year": TEST_YEAR},
            )
            if response.status_code != 302:
                self.stderr.write(self.style.ERROR(
                    f"PayrollRunCreateView did not redirect as expected (status "
                    f"{response.status_code}) — errors: {response.context['errors'] if response.context else '?'}"
                ))
                return
            run = PayrollRun.objects.get(branch=campus, month=TEST_MONTH, year=TEST_YEAR)
            self.stdout.write(f"Created {run}.")
        else:
            self.stdout.write(f"Reusing existing {run} (status={run.status}).")

        if run.status == "draft":
            employee_ids = list(
                Employee.objects.filter(employee_number__startswith="ERP-").values_list("pk", flat=True)
            )
            response = client.post(
                reverse("hr:payroll-run-generate", args=[run.pk]),
                {"employee_id": employee_ids},
            )
            if response.status_code != 200:
                self.stderr.write(self.style.ERROR(
                    f"PayrollRunGeneratePayslipsView returned an unexpected status "
                    f"{response.status_code} — aborting rather than submitting a run "
                    f"that may not have generated any payslips."
                ))
                return
            self.stdout.write(f"Generate payslips: HTTP {response.status_code}")

            response = client.post(reverse("hr:payroll-run-submit", args=[run.pk]))
            if response.status_code != 302:
                self.stderr.write(self.style.ERROR(
                    f"PayrollRunSubmitView did not redirect as expected (status {response.status_code})."
                ))
                return
            run.refresh_from_db()
            self.stdout.write(f"Submitted — status is now {run.status}.")

        if run.status == "ready_for_review":
            response = client.post(reverse("hr:payroll-run-approve", args=[run.pk]))
            if response.status_code != 302:
                self.stderr.write(self.style.ERROR(
                    f"PayrollRunApproveView did not redirect as expected (status {response.status_code})."
                ))
                return
            run.refresh_from_db()
            self.stdout.write(f"Approved & posted — status is now {run.status}.")
        elif run.status == "posted":
            self.stdout.write("Run was already posted.")
        else:
            # Only reachable if a status this script doesn't know about is
            # ever added to PayrollRun.STATUS_CHOICES — fail loudly rather
            # than silently printing an empty comparison table.
            self.stderr.write(self.style.ERROR(
                f'"{run}" is in unexpected status "{run.status}" — not proceeding '
                f"automatically. Resolve it through the normal hr views first."
            ))
            return

        self._print_comparison_table(run)

    def _print_comparison_table(self, run):
        self.stdout.write("\n--- ERP vs TCS OS parity comparison ---\n")
        header = (
            f"{'Employee':<20}{'SSNIT (emp)':>13}{'Tier 2':>10}{'PAYE':>12}"
            f"{'Net Pay':>12}{'SSNIT (er)':>13}  Status"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for payslip in run.payslips.select_related("employee").order_by("employee__employee_number"):
            employee_number = payslip.employee.employee_number
            expected = CONFIRMED_ERP_GROUND_TRUTH.get(employee_number)

            if expected is None:
                self.stdout.write(
                    f"{payslip.employee.full_name:<20}{payslip.ssnit:>13}{payslip.tier2:>10}"
                    f"{payslip.paye:>12}{payslip.net_pay:>12}{payslip.ssnit_employer:>13}  "
                    f"NEEDS CONFIRMATION (no verified ERP payslip on file)"
                )
                continue

            matches = (
                payslip.ssnit == expected["ssnit"]
                and payslip.tier2 == expected["tier2"]
                and payslip.paye == expected["paye"]
                and payslip.net_pay == expected["net_pay"]
                and payslip.ssnit_employer == expected["ssnit_employer"]
            )
            status = "PARITY CONFIRMED" if matches else "MISMATCH — investigate"
            self.stdout.write(
                f"{payslip.employee.full_name:<20}{payslip.ssnit:>13}{payslip.tier2:>10}"
                f"{payslip.paye:>12}{payslip.net_pay:>12}{payslip.ssnit_employer:>13}  {status}"
            )
            if not matches:
                self.stdout.write(f"    expected: {expected}")
