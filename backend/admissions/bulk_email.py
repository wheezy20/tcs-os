"""Phase 6 — bulk/marketing email to Guardians. Deliberately separate from
emails.py: that module is transactional-only (confirmation/offer/draft-resume
emails), sent via SMTP one at a time, and must never be affected by anything
here — a marketing opt-out (Guardian.bulk_email_unsubscribed_at) is checked
only in this module, never in emails.py.

Sends via Resend's HTTP batch API (up to 100 personalized emails per call)
rather than SMTP, since SMTP means one connection/transaction per recipient —
at TCS's real family count (500-2,000) that's minutes of serial sends against
Resend's 10 req/sec team-wide rate limit, not viable synchronously or even as
a tight loop. Raw urllib, no SDK — same low-dependency pattern as
storage.py's Supabase calls; RESEND_API_KEY works as an HTTP Bearer token
here, the same credential already used as the SMTP password.
"""

import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db.models import Q

from .models import Application, EmailCampaignRecipient, Guardian, Student

logger = logging.getLogger(__name__)

RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
RESEND_BATCH_MAX_SIZE = 100  # Resend's own per-request cap

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class ResendBatchError(Exception):
    pass


def render_template(text, context):
    """Simple whitelisted {{name}} substitution — deliberately not Django's
    template engine, which would let a staff-authored subject/body execute
    arbitrary {% %} template logic. A mail-merge doesn't need that, and this
    is safer. An unknown placeholder is left as-is (visibly wrong) rather
    than silently blanked, so a typo shows up in Preview instead of vanishing."""
    def replace(match):
        key = match.group(1)
        return str(context[key]) if key in context else match.group(0)
    return PLACEHOLDER_PATTERN.sub(replace, text)


def build_placeholder_context(guardian):
    students = Student.objects.filter(family_id=guardian.family_id).distinct()
    student_names = ", ".join(s.full_name for s in students) or "your child"
    unsubscribe_url = (
        f"{settings.FRONTEND_BASE_URL}/api/admissions/unsubscribe/"
        f"{guardian.bulk_email_unsubscribe_token}/"
    )
    return {
        "guardian_first_name": guardian.first_name,
        "guardian_full_name": guardian.full_name,
        "student_names": student_names,
        "unsubscribe_link": unsubscribe_url,
    }


def compute_recipients(campaign):
    """The base set is every Guardian not currently unsubscribed — opt-out,
    not an explicit opt-in list, per the approved plan. filter_* fields
    narrow that further (AND semantics); blank means no filter on that
    dimension. Called once, at Send time — see EmailCampaign's docstring for
    why this isn't recomputed later."""
    qs = Guardian.objects.filter(bulk_email_unsubscribed_at__isnull=True)

    app_filter = Q()
    if campaign.filter_stage:
        app_filter &= Q(family__students__applications__stage=campaign.filter_stage)
    if campaign.filter_academic_year:
        app_filter &= Q(family__students__applications__academic_year=campaign.filter_academic_year)
    if campaign.filter_campus_id:
        app_filter &= Q(family__students__applications__campus_id=campaign.filter_campus_id)

    if app_filter:
        qs = qs.filter(app_filter)

    return qs.distinct()


def send_resend_batch(payload):
    """payload: list of Resend email dicts (from/to/subject/text/headers).
    Returns the parsed response dict on success. Raises ResendBatchError on
    any failure — confirmed empirically (not assumed) that Resend's batch
    endpoint is all-or-nothing at the HTTP level: either every email in the
    request is accepted (200, one {"id": ...} per input item, same order as
    the request) or the whole call fails (e.g. 403 for an unverified
    domain) — there's no partial per-item success/failure shape to handle."""
    data = json.dumps(payload).encode()
    req = Request(
        RESEND_BATCH_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
            # Without an explicit User-Agent, urllib's default
            # ("Python-urllib/3.x") gets blocked outright by Cloudflare
            # (fronting api.resend.com) as bot traffic — a real 403 "error
            # code: 1010", confirmed directly, not assumed. storage.py's
            # Supabase calls happen not to hit this (different Cloudflare
            # config on their side), so this is specific to this endpoint.
            "User-Agent": "tcs-os-admissions/1.0",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise ResendBatchError(f"Resend batch send failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise ResendBatchError(f"Could not reach Resend: {exc}") from exc


def build_batch_payload(recipients):
    """recipients: EmailCampaignRecipient queryset (select_related('guardian')
    is the caller's job). Returns a list of Resend email dicts, same order
    as recipients, so a caller can zip() the response back onto each row."""
    payload = []
    for recipient in recipients:
        context = build_placeholder_context(recipient.guardian)
        subject = render_template(recipient.campaign.subject, context)
        body = render_template(recipient.campaign.body, context)
        payload.append({
            "from": settings.BULK_EMAIL_FROM_EMAIL,
            "to": [recipient.email],
            "subject": subject,
            "text": body,
            "headers": {
                "List-Unsubscribe": f"<{context['unsubscribe_link']}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        })
    return payload


def enqueue_campaign_send(campaign):
    """Splits the campaign's pending recipients into batches of
    RESEND_BATCH_MAX_SIZE and creates one Cloud Task per batch. Imports
    google.cloud.tasks_v2 lazily so the rest of this module (template
    rendering, recipient computation — all independently testable) doesn't
    require GCP credentials just to import."""
    from google.cloud import tasks_v2

    recipient_ids = list(
        campaign.recipients.filter(status="pending").values_list("id", flat=True)
    )
    if not recipient_ids:
        return 0

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(settings.GCP_PROJECT_ID, settings.CLOUD_TASKS_LOCATION, settings.CLOUD_TASKS_QUEUE)
    url = f"{settings.FRONTEND_BASE_URL}/api/admissions/internal/send-campaign-batch/"

    task_count = 0
    for i in range(0, len(recipient_ids), RESEND_BATCH_MAX_SIZE):
        batch_ids = recipient_ids[i:i + RESEND_BATCH_MAX_SIZE]
        body = json.dumps({"campaign_id": campaign.id, "recipient_ids": batch_ids}).encode()
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": url,
                "headers": {
                    "Content-Type": "application/json",
                    "X-Internal-Secret": settings.BULK_EMAIL_INTERNAL_SECRET,
                },
                "body": body,
            }
        }
        client.create_task(request={"parent": parent, "task": task})
        task_count += 1

    return task_count
