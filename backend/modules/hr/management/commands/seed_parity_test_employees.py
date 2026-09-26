"""Merge Phase 3, Session 1 — one-off test setup for the ERP/TCS OS
payroll parity check. NOT a migration: this is dummy/test data (the ERP
itself has only ever run test payroll — see docs/PLAN.md's Merge Phase 3
note), so it belongs in a rerunnable, deletable management command, not
permanent seed history.

Data below is copied verbatim from the ERP's own seed data, not invented:
  - employees: ~/projects/tcs-erp/supabase/seed.sql, the
    `insert into public.employees` block (lines ~76-86).
  - pay configs: same file, the `insert into public.employee_pay_config`
    block (lines ~1717-1723).

Three fields are reasoned inferences, not literal copies from the ERP,
flagged here rather than silently presented as verbatim ERP data:
  - `payment_method="Bank Transfer"` — the ERP's employee_pay_config has
    no payment_method column at all; every row there instead carries a
    bank + account_no, meaningful only for a bank-transfer payout, so
    this is the one value consistent with the source data.
  - `employment_type="Full-time"` — the ERP's employees table
    (tcs-erp/supabase/migrations/20260909130000_employees_and_approval_
    workflow.sql) has no employment_type column either; invented, since
    every seeded employee here is in fact full-time.
  - `start_date` — same story: no hire-date column in the ERP schema.
    Set to PAY_CONFIG_EFFECTIVE_FROM as a plausible placeholder.
None of these three feed calculate_payslip() — they exist only for
Session 7's document-generation merge fields — so none of them affect
this session's actual parity comparison.

Idempotent: get_or_create keyed on Employee.employee_number (derived
from the ERP's own UUID, kept as a stable cross-system reference per
Employee.employee_number's own help_text) and on
(employee, effective_from) for EmployeePayConfig.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from modules.hr.models import Employee, EmployeePayConfig

# (erp uuid suffix, full name, position, department, basic_salary,
#  pays_ssnit, pays_tier2, pays_paye)
ERP_EMPLOYEES = [
    ("0004", "Emmanuel Ansah", "Head Teacher", "Administration", Decimal("6500.00"), True, True, True),
    ("0003", "Ebenezer Addo", "Accountant", "Administration", Decimal("4200.00"), True, True, True),
    ("0001", "Ama Owusu", "Class Teacher", "Lower Primary", Decimal("2800.00"), True, True, True),
    ("0002", "Kojo Boadu", "Teaching Assistant", "Lower Primary", Decimal("1500.00"), False, False, False),
]

PAY_CONFIG_EFFECTIVE_FROM = date(2026, 1, 1)


class Command(BaseCommand):
    help = (
        "Create Employee + EmployeePayConfig rows matching the ERP's real "
        "seeded dummy employees, for the Merge Phase 3 parity check. "
        "Idempotent — safe to rerun. Test setup only, not permanent seed data."
    )

    def handle(self, *args, **options):
        for erp_suffix, full_name, position, department, basic_salary, pays_ssnit, pays_tier2, pays_paye in ERP_EMPLOYEES:
            first_name, _, surname = full_name.partition(" ")
            employee, created = Employee.objects.get_or_create(
                employee_number=f"ERP-{erp_suffix}",
                defaults={
                    "first_name": first_name,
                    "surname": surname,
                    "basic_salary": basic_salary,
                    "employment_status": "active",
                    "position": position,
                    "department": department,
                    "employment_type": "Full-time",
                    "payment_method": "Bank Transfer",
                    "start_date": PAY_CONFIG_EFFECTIVE_FROM,
                },
            )
            self.stdout.write(
                f"  {'created' if created else 'already existed'}: Employee {employee}"
            )

            pay_config, pc_created = EmployeePayConfig.objects.get_or_create(
                employee=employee,
                effective_from=PAY_CONFIG_EFFECTIVE_FROM,
                defaults={
                    "basic_salary": basic_salary,
                    "pays_ssnit": pays_ssnit,
                    "pays_tier2": pays_tier2,
                    "pays_paye": pays_paye,
                    "approval_status": "approved",
                },
            )
            self.stdout.write(
                f"    {'created' if pc_created else 'already existed'}: "
                f"EmployeePayConfig(basic_salary={pay_config.basic_salary}, "
                f"pays_ssnit={pay_config.pays_ssnit}, pays_tier2={pay_config.pays_tier2}, "
                f"pays_paye={pay_config.pays_paye}, approval_status={pay_config.approval_status})"
            )

        self.stdout.write(self.style.SUCCESS(
            f"Done — {len(ERP_EMPLOYEES)} employee(s) and pay config(s) in place for the parity test."
        ))
