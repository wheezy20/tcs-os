from django.core.management.base import BaseCommand

from modules.hr import storage


class Command(BaseCommand):
    help = (
        "Set the hr-documents Supabase Storage bucket's file_size_limit and "
        "allowed_mime_types (PDF only — every document here is server-rendered, "
        "never a client upload of an arbitrary file type). Run once per "
        "environment after the bucket itself is created (a manual Supabase "
        "dashboard/API step, not this command's job), and again any time the "
        "limit changes. Mirrors admissions' configure_storage_bucket."
    )

    def handle(self, *args, **options):
        storage.configure_bucket_limits()
        self.stdout.write(self.style.SUCCESS(
            "hr-documents bucket updated: file_size_limit=10MB, allowed_mime_types=['application/pdf']"
        ))
