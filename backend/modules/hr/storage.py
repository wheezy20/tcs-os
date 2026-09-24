"""Supabase Storage helpers for the hr-documents bucket (Session 7).

Mirrors modules/admissions/storage.py's private-bucket + signed-URL-only
convention exactly — same Supabase project (SUPABASE_URL/
SUPABASE_SERVICE_ROLE_KEY), a second bucket namespaced for this module
(settings.HR_DOCUMENT_STORAGE_BUCKET). Never getPublicUrl()-equivalent;
every read goes through a freshly-minted signed URL.

One real difference from admissions' flow, worth naming rather than
copying the wrong half of that pattern: admissions mints a signed
*upload* URL so a browser can PUT a file directly (create_upload_target())
— that shape exists because a parent's browser is the one with the file.
Here, Django itself renders the PDF server-side and already holds the
bytes, so there's nothing for a browser to PUT — this module uploads
directly via Supabase Storage's plain POST /object/{bucket}/{path}
endpoint instead of minting a signed upload URL for a client to use.
"""

import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

READ_URL_EXPIRES_IN = 60 * 5  # 5 minutes — minted fresh per page view, not stored


class HrStorageError(Exception):
    pass


def _bucket():
    return settings.HR_DOCUMENT_STORAGE_BUCKET


def upload_pdf_bytes(employee, document_kind, pdf_bytes):
    """Uploads an already-rendered PDF and returns its storage_path. The
    path is generated here (a fresh uuid4, not anything client-supplied),
    matching admissions.storage.create_upload_target()'s "never trust a
    caller for the path" convention."""
    storage_path = f"{employee.pk}/{document_kind}/{uuid.uuid4()}.pdf"
    url = f"{settings.SUPABASE_URL}/storage/v1/object/{_bucket()}/{storage_path}"
    req = Request(
        url,
        data=pdf_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "application/pdf",
        },
    )
    try:
        with urlopen(req, timeout=15):
            pass
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise HrStorageError(f"Supabase Storage upload failed ({exc.code}): {detail}") from exc
    return storage_path


def create_read_url(storage_path):
    """Mint a short-lived signed download URL for an already-uploaded
    document. Called on demand each time a "View" link is followed —
    never cached/stored, same reasoning as admissions.storage's own
    create_read_url()."""
    url = f"{settings.SUPABASE_URL}/storage/v1/object/sign/{_bucket()}/{storage_path}"
    req = Request(
        url,
        data=json.dumps({"expiresIn": READ_URL_EXPIRES_IN}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise HrStorageError(f"Supabase Storage sign request failed ({exc.code}): {detail}") from exc
    return f"{settings.SUPABASE_URL}/storage/v1{result['signedURL']}"


def configure_bucket_limits():
    """Push a PDF-only, generation-size-appropriate limit onto the bucket
    itself — mirrors admissions.storage.configure_bucket_limits(). PDFs
    only (not admissions' PDF/JPG/PNG allow-list, since these are always
    server-rendered PDFs, never a client upload of an arbitrary file
    type). Not called automatically; run `manage.py configure_hr_storage_bucket`
    after changing the limit or after the bucket is first created."""
    url = f"{settings.SUPABASE_URL}/storage/v1/bucket/{_bucket()}"
    req = Request(
        url,
        data=json.dumps({"file_size_limit": "10MB", "allowed_mime_types": ["application/pdf"]}).encode(),
        method="PUT",
        headers={
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=10):
            pass
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise HrStorageError(f"Supabase Storage bucket config failed ({exc.code}): {detail}") from exc
