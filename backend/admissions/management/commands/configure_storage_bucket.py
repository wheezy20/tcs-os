from django.core.management.base import BaseCommand

from admissions import storage


class Command(BaseCommand):
    help = (
        "Set the Supabase Storage bucket's file_size_limit and allowed_mime_types "
        "to match MAX_UPLOAD_SIZE_MB and storage.ALLOWED_UPLOAD_EXTENSIONS. Run "
        "once per environment (and again any time those settings change) — this "
        "is the enforcement layer that can't be bypassed by a client lying about "
        "file_size in the upload-url request."
    )

    def handle(self, *args, **options):
        size_limit, mime_types = storage.configure_bucket_limits()
        self.stdout.write(self.style.SUCCESS(
            f"Bucket updated: file_size_limit={size_limit}, allowed_mime_types={mime_types}"
        ))
