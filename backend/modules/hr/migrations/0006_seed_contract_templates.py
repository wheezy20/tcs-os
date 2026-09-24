# Session 7 — seeds one blank ContractTemplate row per document kind.
# Deliberately html_body="" for all four: no letter/contract prose is
# invented by this port (see the model's own docstring and
# documents.generate_document(), which refuses to generate against a
# blank html_body). A real HR staff member has to author the actual text
# in admin before any of these four kinds is usable.
#
# get_or_create() keyed on category (its own unique=True field) — same
# idempotent pattern as 0002_seed_statutory_data.py.

from django.db import migrations

CATEGORIES = ["Appointment Letter", "Probation Letter", "Contract-Teaching", "Contract-Non-Teaching"]


def seed_contract_templates(apps, schema_editor):
    ContractTemplate = apps.get_model("hr", "ContractTemplate")
    for category in CATEGORIES:
        ContractTemplate.objects.get_or_create(category=category, defaults={"html_body": ""})


def remove_contract_templates(apps, schema_editor):
    ContractTemplate = apps.get_model("hr", "ContractTemplate")
    ContractTemplate.objects.filter(category__in=CATEGORIES, html_body="").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("hr", "0005_employee_department_employee_employment_type_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_contract_templates, remove_contract_templates),
    ]
