"""Supabase Storage signed-URL helpers for the finance-receipts bucket
(Session 3) — mirrors modules/admissions/storage.py's private-bucket,
signed-URL-only pattern exactly (same two-step handshake: mint a signed
upload URL here, the browser PUTs the raw file bytes directly to
Supabase with no auth of its own), since a receipt is a client-uploaded
photo/scan, the same shape as an admissions Document — unlike hr's
employee-generated documents, which are rendered server-side and
uploaded directly (see modules/hr/storage.py's own docstring on that
distinction).
"""

import json
import re
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

READ_URL_EXPIRES_IN = 60 * 5  # 5 minutes — minted fresh per page view, not stored

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

# Same allow-list as admissions' Document uploads — receipts are
# scanned/photographed the same way.
ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
EXTENSION_MIME_TYPES = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


class FinanceStorageError(Exception):
    pass


def _sanitize_filename(filename):
    name = filename.strip().replace("/", "_").replace("\\", "_")
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    return name[-150:] or "file"


def _request(method, path, body=None):
    url = f"{settings.SUPABASE_URL}/storage/v1{path}"
    data = json.dumps(body).encode() if body is not None else b"{}"
    req = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise FinanceStorageError(f"Supabase Storage request failed ({exc.code}): {detail}") from exc


def create_upload_target(filename):
    """Mint a fresh storage path + signed upload URL for a new receipt.
    Returns (storage_path, upload_url). The frontend PUTs the raw file
    bytes to upload_url with no auth headers of its own. The path is
    generated here, never trusted from the client."""
    storage_path = f"{uuid.uuid4()}/{_sanitize_filename(filename)}"
    bucket = settings.FINANCE_RECEIPTS_STORAGE_BUCKET

    result = _request("POST", f"/object/upload/sign/{bucket}/{storage_path}")
    upload_url = f"{settings.SUPABASE_URL}/storage/v1{result['url']}"
    return storage_path, upload_url


def create_read_url(storage_path):
    """Mint a short-lived signed download URL for an already-uploaded
    receipt. Called on demand each time a "View receipt" link is
    followed — never cached/stored, same reasoning as admissions'."""
    bucket = settings.FINANCE_RECEIPTS_STORAGE_BUCKET
    result = _request(
        "POST",
        f"/object/sign/{bucket}/{storage_path}",
        {"expiresIn": READ_URL_EXPIRES_IN},
    )
    return f"{settings.SUPABASE_URL}/storage/v1{result['signedURL']}"


def configure_bucket_limits():
    """Push MAX_UPLOAD_SIZE_MB and the allowed MIME types down onto the
    finance-receipts bucket itself — layer 3 of the three-layer upload-
    validation convention (docs/CONSTRAINTS.md), the only one that checks
    the real bytes on the actual PUT, so it can't be bypassed by a client
    lying about file_size when requesting the signed URL. Mirrors
    admissions.storage.configure_bucket_limits() exactly. Idempotent —
    safe to re-run any time the allow-list or size limit changes. Not
    called automatically; run `manage.py configure_finance_storage_bucket`
    after changing either setting or after the bucket is first created."""
    bucket = settings.FINANCE_RECEIPTS_STORAGE_BUCKET
    size_limit = f"{settings.MAX_UPLOAD_SIZE_MB}MB"
    mime_types = sorted(set(EXTENSION_MIME_TYPES.values()))
    _request(
        "PUT",
        f"/bucket/{bucket}",
        {"file_size_limit": size_limit, "allowed_mime_types": mime_types},
    )
    return size_limit, mime_types
