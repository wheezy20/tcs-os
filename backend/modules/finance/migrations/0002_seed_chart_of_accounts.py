# Seeds the real TCS ERP chart of accounts — 46 rows total, pulled
# verbatim from the ERP's own migrations (not invented or approximated):
#
#   - 41 accounts from
#     tcs-erp/supabase/migrations/20260819090000_seed_gap_accounts_expense_categories.sql
#     (the base chart, plus its Session 14 additions 5210/5220 and its
#     Session 19 addition 1150 — all three already folded into that
#     single migration's insert block by the time this was read).
#   - 5 payroll-related accounts, added by later ERP migrations, even
#     though this Django port's hr module doesn't post to the ledger yet
#     (Merge Phase 2 Session 2 will wire that up) — seeding them now so
#     they already exist when that session needs them:
#       2310 SSNIT Payable, 2320 Provident Fund (Tier 2) Payable,
#       2330 PAYE Payable, 4910 Staff Fines Recovered
#         (tcs-erp/supabase/migrations/20260909110000_post_payroll_run.sql)
#       5145 Employer SSNIT Contribution
#         (tcs-erp/supabase/migrations/20260909120000_payslip_employer_ssnit.sql)
#
# A 6th payroll account code, 5146 (Employer Tier 2 Contribution), was
# named in the original request but does not exist anywhere in the
# ERP's own migrations (confirmed by a full-repo search) — not seeded
# here. Confirmed 2026-09-26 (not just flagged): it was a byproduct of
# the reverted Tier 2 incident (docs/DESIGN.md's payroll section — an
# earlier pass that briefly had the Ghana scheme backwards, since
# undone), not a real ERP account. The confirmed-correct Ghana Tier 2
# scheme is 100% employee-funded, so there is no employer-side Tier 2
# contribution for an "Employer Tier 2" expense account to represent.
# Should not be created.
#
# created_by is left null for every seeded row — mirroring the ERP's own
# resolution of this identical problem (accounts.created_by dropped to
# nullable in its 20260819090000 migration) rather than attributing
# system-seeded rows to an arbitrary/fabricated user.
#
# get_or_create() keyed on code (its own unique=True field) — same
# idempotent pattern as hr/migrations/0002_seed_statutory_data.py and
# admissions/migrations/0009_seed_campuses.py.

from django.db import migrations

# (code, name, category, subtype, description)
ACCOUNTS = [
    # Assets
    ("1000", "Cash on Hand", "Assets", "Current Asset", "Physical cash held at the till/store."),
    ("1010", "Cash in Bank", "Assets", "Current Asset", "Business bank account balances."),
    ("1020", "Mobile Money Float", "Assets", "Current Asset", "Mobile money wallet balance used for sales and deposits."),
    ("1100", "Accounts Receivable", "Assets", "Current Asset", "Amounts owed by customers on credit invoices."),
    ("1150", "WHT Credit Receivable", "Assets", "Current Asset",
     "Withholding tax deducted by customers on our invoices, claimable back against our own tax liability."),
    ("1200", "Inventory", "Assets", "Current Asset", "Cost value of stock on hand across all product categories."),
    ("1300", "Prepaid Expenses", "Assets", "Current Asset", "Expenses paid in advance, e.g. rent or insurance."),
    ("1350", "Advances to Staff", "Assets", "Current Asset", "Salary advances recoverable from future wages."),
    ("1400", "Furniture & Fittings", "Assets", "Fixed Asset", "Shop fixtures, shelving, counters and office furniture."),
    ("1410", "Delivery Vehicles", "Assets", "Fixed Asset", "Vans and trucks used for customer deliveries."),
    ("1420", "Warehouse Equipment", "Assets", "Fixed Asset", "Forklifts, pallet trucks and other handling equipment."),
    ("1490", "Accumulated Depreciation", "Assets", "Contra-Asset", "Cumulative depreciation against fixed assets above."),
    # Liabilities
    ("2000", "Accounts Payable", "Liabilities", "Current Liability", "Amounts owed to suppliers for goods and services."),
    ("2100", "VAT Payable", "Liabilities", "Current Liability", "VAT collected on sales, owed to the tax authority."),
    ("2200", "Accrued Expenses", "Liabilities", "Current Liability", "Expenses incurred but not yet paid or invoiced."),
    ("2300", "Salaries & Wages Payable", "Liabilities", "Current Liability", "Staff pay earned but not yet disbursed."),
    ("2310", "SSNIT Payable", "Liabilities", "Current Liability",
     "Employee SSNIT contributions withheld from payroll, owed to SSNIT."),
    ("2320", "Provident Fund (Tier 2) Payable", "Liabilities", "Current Liability",
     "Employee Tier 2 provident fund contributions withheld from payroll, owed to the scheme trustee."),
    ("2330", "PAYE Payable", "Liabilities", "Current Liability",
     "PAYE income tax withheld from staff pay, owed to the Ghana Revenue Authority."),
    ("2400", "Customer Store Credit", "Liabilities", "Current Liability",
     "Store credit issued on returns/exchanges, owed back to customers."),
    ("2500", "Short-Term Loans Payable", "Liabilities", "Current Liability", "Borrowings due within one year."),
    ("2600", "Long-Term Loans Payable", "Liabilities", "Long-term Liability", "Borrowings due beyond one year."),
    # Equity
    ("3000", "Owner's Capital", "Equity", "Equity", "Capital invested into the business by its owner(s)."),
    ("3100", "Retained Earnings", "Equity", "Equity", "Accumulated profits retained in the business."),
    ("3200", "Owner's Drawings", "Equity", "Contra-Equity", "Cash or goods withdrawn by the owner for personal use."),
    # Revenue
    ("4000", "Sales Revenue", "Revenue", "Revenue", "Revenue from POS and invoiced sales of goods."),
    ("4100", "Sales Returns & Allowances", "Revenue", "Contra-Revenue", "Reductions to revenue from returned goods."),
    ("4200", "Sales Discounts Given", "Revenue", "Contra-Revenue", "Reductions to revenue from discounts applied at sale."),
    ("4900", "Other Income", "Revenue", "Revenue", "Income outside of ordinary sales activity."),
    ("4910", "Staff Fines Recovered", "Revenue", "Revenue",
     "Fines and penalties deducted from staff pay. Kept separate from 4900 Other Income for visibility into recovery."),
    # Expenses
    ("5000", "Cost of Goods Sold", "Expenses", "Cost of Goods Sold", "Cost value of inventory sold."),
    ("5100", "Rent Expense", "Expenses", "Operating Expense", "Rent for the store, warehouse or offices."),
    ("5110", "Utilities Expense", "Expenses", "Operating Expense", "Electricity, water and similar utility bills."),
    ("5120", "Transport Expense", "Expenses", "Operating Expense", "Fares and haulage for deliveries and errands."),
    ("5130", "Fuel Expense", "Expenses", "Operating Expense", "Fuel for delivery vehicles and equipment."),
    ("5140", "Salaries & Wages Expense", "Expenses", "Operating Expense", "Pay for permanent staff."),
    ("5145", "Employer SSNIT Contribution", "Expenses", "Operating Expense",
     "The employer's 13% SSNIT contribution on staff basic salaries. Kept separate from 5140 Salaries & Wages "
     "Expense so total cost of employment (5140 + 5145) is visible on its own."),
    ("5150", "Casual Labour Expense", "Expenses", "Operating Expense",
     "Pay for day labourers, e.g. loading/offloading crews."),
    ("5160", "Repairs & Maintenance Expense", "Expenses", "Operating Expense",
     "Upkeep of vehicles, equipment and premises."),
    ("5170", "Office & Store Supplies Expense", "Expenses", "Operating Expense",
     "Consumables such as receipt rolls, packing materials, stationery."),
    ("5180", "Marketing & Advertising Expense", "Expenses", "Operating Expense", "Promotion and advertising spend."),
    ("5190", "Bank Charges & Fees", "Expenses", "Operating Expense", "Bank and mobile money transaction charges."),
    ("5200", "Depreciation Expense", "Expenses", "Operating Expense",
     "Periodic depreciation charge against fixed assets."),
    ("5210", "Inventory Shrinkage & Loss", "Expenses", "Operating Expense",
     "Stock lost to damage, theft or unexplained shrinkage, kept separate from Cost of Goods Sold so loss trends "
     "stay visible on their own."),
    ("5220", "Cash Over/Short", "Expenses", "Operating Expense",
     "Reconciles counted till cash against the books at End of Day close; can run either a debit (net shortages) "
     "or credit (net overages) balance over time."),
    ("5900", "Miscellaneous Expense", "Expenses", "Operating Expense",
     "Minor expenses not covered by another category."),
]


def seed_chart_of_accounts(apps, schema_editor):
    Account = apps.get_model("finance", "Account")
    for code, name, category, subtype, description in ACCOUNTS:
        Account.objects.get_or_create(
            code=code,
            defaults={
                "name": name, "category": category, "subtype": subtype, "description": description,
                "created_by": None,
            },
        )


def remove_chart_of_accounts(apps, schema_editor):
    Account = apps.get_model("finance", "Account")
    Account.objects.filter(code__in=[row[0] for row in ACCOUNTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_chart_of_accounts, remove_chart_of_accounts),
    ]
