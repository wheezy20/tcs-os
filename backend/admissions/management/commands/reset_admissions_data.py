from django.core.management.base import BaseCommand
from django.db import connection

from admissions.models import (
    Application,
    Document,
    Family,
    Guardian,
    Note,
    ReferenceCounter,
    Student,
)


class Command(BaseCommand):
    help = (
        "DESTRUCTIVE, IRREVERSIBLE. Truncates every admissions table (Family, Guardian, "
        "Student, Application, Document, Note, ReferenceCounter) and resets their "
        "auto-increment ID sequences back to 1. Intended for wiping Phase 1/2 test data "
        "immediately before going live — never run this against real admissions data. "
        "Always prompts for confirmation; there is no flag to skip the prompt."
    )

    # Order doesn't matter for correctness (TRUNCATE ... CASCADE handles FK dependencies
    # regardless of listing order), but this reads parent-to-child for clarity of what's
    # actually being wiped.
    MODELS = [Family, Guardian, Student, Application, Document, Note, ReferenceCounter]

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING(
            "This will PERMANENTLY DELETE ALL ROWS in the tables below and reset their "
            "ID sequences back to 1. This cannot be undone.\n"
        ))
        for model in self.MODELS:
            self.stdout.write(f"  - {model._meta.db_table} ({model.objects.count()} rows)")
        self.stdout.write("")

        confirmation = input('Type "yes" to continue, anything else to abort: ')
        if confirmation.strip().lower() != "yes":
            self.stdout.write(self.style.NOTICE("Aborted. No changes made."))
            return

        table_names = ", ".join(f'"{model._meta.db_table}"' for model in self.MODELS)
        with connection.cursor() as cursor:
            cursor.execute(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE;")

        self.stdout.write(self.style.SUCCESS(
            f"Truncated {len(self.MODELS)} table(s) and reset their ID sequences to 1."
        ))
