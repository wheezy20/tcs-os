"""Supabase Storage signed-URL helpers for the admissions-documents bucket.

The bucket is private, so uploads/downloads go through short-lived signed URLs
rather than a public URL. The service role key never leaves this process —
it's only used for the two server-to-server calls that mint signed URLs; the
resulting URL is self-authorizing (the signing token lives in the query
string), so the browser needs no key of its own to use it.

See docs/admissions/02-stack-and-schema.md.
"""

import json
import re
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

READ_URL_EXPIRES_IN = 60 * 5  # 5 minutes — minted fresh per admin page view, not stored

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class SupabaseStorageError(Exception):
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
        raise SupabaseStorageError(f"Supabase Storage request failed ({exc.code}): {detail}") from exc


def create_upload_target(document_type, filename):
    """Mint a fresh storage path + signed upload URL for a new document.

    Returns (storage_path, upload_url). The frontend PUTs the raw file bytes
    to upload_url with no auth headers of its own. The path is generated
    here, never trusted from the client, so a caller can't write outside its
    own namespace or overwrite another submission's file.
    """
    storage_path = f"{uuid.uuid4()}/{document_type}/{_sanitize_filename(filename)}"
    bucket = settings.SUPABASE_STORAGE_BUCKET

    result = _request("POST", f"/object/upload/sign/{bucket}/{storage_path}")
    upload_url = f"{settings.SUPABASE_URL}/storage/v1{result['url']}"
    return storage_path, upload_url


def create_read_url(storage_path):
    """Mint a short-lived signed download URL for an already-uploaded file.
    Called on demand each time admin renders a document link — Supabase 404s
    if you sign a path before the object exists, and a URL stored long-term
    would eventually expire silently, so nothing is cached here."""
    bucket = settings.SUPABASE_STORAGE_BUCKET
    result = _request(
        "POST",
        f"/object/sign/{bucket}/{storage_path}",
        {"expiresIn": READ_URL_EXPIRES_IN},
    )
    return f"{settings.SUPABASE_URL}/storage/v1{result['signedURL']}"
