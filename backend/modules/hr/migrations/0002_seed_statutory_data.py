# Seeds the real, confirmed-correct statutory config — GRA's 2024 monthly
# resident-individual PAYE bands and the Act 766 SSNIT/Tier 2 rates — both
# effective_from 2025-01-01. A data migration (not a fixture) so it runs
# automatically on `migrate` and stays version-controlled alongside the
# schema, same pattern as admissions/migrations/0009_seed_campuses.py.
#
# PAYEBand note: GRA's own published table is internally inconsistent
# between its cumulative-sum column and its literal band label for the top
# two bands — arithmetic on the cumulative column implies the 30% band ends
# at 50,416.67, but the table's own label says "Exceeding 50,000.00". This
# uses the literal 50,000.00 cutoff, matching what the ERP's own
# 20260918130000_paye_bands_gra_2024.sql migration already resolved this
# to — kept consistent with the ERP rather than re-deriving from the
# inconsistent source table.
#
# get_or_create() keyed on each model's own natural-uniqueness field(s)
# (PAYEBand: effective_from + band_order, its unique_together; StatutoryRate:
# effective_from, its unique=True) — idempotent, safe to re-run, and the DB
# constraints back it up.

from decimal import Decimal

from django.db import migrations

PAYE_BANDS_2025 = [
    # (band_order, lower_bound, upper_bound, rate)
    (1, "0", "490.00", "0.0000"),
    (2, "490.00", "600.00", "0.0500"),
    (3, "600.00", "730.00", "0.1000"),
    (4, "730.00", "3896.67", "0.1750"),
    (5, "3896.67", "19896.67", "0.2500"),
    (6, "19896.67", "50000.00", "0.3000"),
    (7, "50000.00", None, "0.3500"),
]

EFFECTIVE_FROM = "2025-01-01"


def seed_statutory_data(apps, schema_editor):
    PAYEBand = apps.get_model("hr", "PAYEBand")
    StatutoryRate = apps.get_model("hr", "StatutoryRate")

    for band_order, lower, upper, rate in PAYE_BANDS_2025:
        PAYEBand.objects.get_or_create(
            effective_from=EFFECTIVE_FROM,
            band_order=band_order,
            defaults={
                "lower_bound": Decimal(lower),
                "upper_bound": Decimal(upper) if upper is not None else None,
                "rate": Decimal(rate),
            },
        )

    StatutoryRate.objects.get_or_create(
        effective_from=EFFECTIVE_FROM,
        defaults={
            "ssnit_employee_pct": Decimal("0.0050"),
            "ssnit_employer_pct": Decimal("0.1300"),
            "tier2_employee_pct": Decimal("0.0500"),
        },
    )


def remove_statutory_data(apps, schema_editor):
    PAYEBand = apps.get_model("hr", "PAYEBand")
    StatutoryRate = apps.get_model("hr", "StatutoryRate")

    PAYEBand.objects.filter(effective_from=EFFECTIVE_FROM).delete()
    StatutoryRate.objects.filter(effective_from=EFFECTIVE_FROM).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_statutory_data, remove_statutory_data),
    ]
