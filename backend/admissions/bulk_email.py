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
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator
from django.db.models import Count, Q
from django.utils import timezone

from .models import Application, EmailCampaignRecipient, Guardian, Lead, Student

logger = logging.getLogger(__name__)

RESEND_BATCH_URL = "https://api.resend.com/emails/batch"
RESEND_BATCH_MAX_SIZE = 100  # Resend's own per-request cap

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")

_EMAIL_VALIDATOR = EmailValidator()

# Domains Resend's batch API rejects outright, taking down the entire batch —
# confirmed directly, not guessed: one @example.com address in an 11-row
# batch failed all 11, with Resend's own error naming "domains like
# `example.com`" as the reason. These are well-known placeholder domains
# (RFC 2606 / common seed-data conventions), never real recipient addresses.
PLACEHOLDER_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.edu", "test.com"}


class ResendBatchError(Exception):
    pass


def invalid_email_reason(email):
    """Returns a short reason a recipient's address would be rejected before
    it's ever sent to Resend, or None if it looks sendable. Deliberately
    narrow — catches obviously malformed syntax and known placeholder
    domains only. This is NOT a deliverability guarantee: a syntactically
    valid address at a domain that doesn't actually accept mail still bounces
    *after* sending, which needs bounce-webhook handling (out of scope here,
    see 02-stack-and-schema.md). The point is just to stop the class of bad
    address that currently takes an entire otherwise-valid batch down with
    it, not to replace real deliverability tracking."""
    try:
        _EMAIL_VALIDATOR(email)
    except ValidationError:
        return "Malformed email address."
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in PLACEHOLDER_EMAIL_DOMAINS:
        return f"Placeholder/example domain ({domain}) — not a real deliverable address."
    return None


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


def _unsubscribe_url(token):
    return f"{settings.FRONTEND_BASE_URL}/api/admissions/unsubscribe/{token}/"


def build_placeholder_context(guardian):
    students = Student.objects.filter(family_id=guardian.family_id).distinct()
    student_names = ", ".join(s.full_name for s in students) or "your child"
    return {
        # Neutral names — work for a Guardian or a Lead recipient.
        "recipient_first_name": guardian.first_name,
        "recipient_full_name": guardian.full_name,
        # Back-compat aliases kept so existing campaign bodies still render.
        "guardian_first_name": guardian.first_name,
        "guardian_full_name": guardian.full_name,
        "student_names": student_names,
        "unsubscribe_link": _unsubscribe_url(guardian.bulk_email_unsubscribe_token),
    }


def build_lead_placeholder_context(lead):
    """Same keys as build_placeholder_context so a campaign body renders
    identically whether the recipient is a Guardian or a Lead. A Lead has no
    family/children, so {{student_names}} falls back to the same "your child"
    string an empty Guardian family would produce."""
    return {
        "recipient_first_name": lead.first_name,
        "recipient_full_name": lead.full_name,
        "guardian_first_name": lead.first_name,
        "guardian_full_name": lead.full_name,
        "student_names": "your child",
        "unsubscribe_link": _unsubscribe_url(lead.bulk_email_unsubscribe_token),
    }


def context_for_recipient(recipient):
    """recipient: an EmailCampaignRecipient (saved or unsaved). Dispatches on
    which target it carries — exactly one of guardian/lead is set."""
    if recipient.guardian_id is not None:
        return build_placeholder_context(recipient.guardian)
    return build_lead_placeholder_context(recipient.lead)


def guardian_recipients(campaign):
    """Guardian pool: every Guardian not currently unsubscribed (opt-out, not
    an explicit opt-in list), narrowed by the filter_* fields (AND
    semantics; blank = no filter). Empty when the campaign's audience
    excludes guardians."""
    if campaign.audience not in ("guardians", "both"):
        return Guardian.objects.none()

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


def lead_recipients(campaign):
    """Lead pool: opted-in, not-unsubscribed Leads, optionally narrowed to a
    single source. Empty when the campaign's audience excludes leads. The
    Guardian filter_* fields (stage/year/campus) do not apply — a Lead has
    none of that data."""
    if campaign.audience not in ("leads", "both"):
        return Lead.objects.none()

    qs = Lead.objects.filter(
        consent_to_marketing=True,
        bulk_email_unsubscribed_at__isnull=True,
    )
    if campaign.filter_lead_source:
        qs = qs.filter(source=campaign.filter_lead_source)
    return qs


def compute_recipient_rows(campaign):
    """Returns a list of *unsaved* EmailCampaignRecipient rows for the
    campaign's current audience — the caller (send_campaign) bulk_creates
    them. Called once, at Send time; see EmailCampaign's docstring for why
    this isn't recomputed later.

    De-dupe rule for audience="both": if the same email address is both a
    Guardian and a Lead, the Guardian row wins and the Lead row is dropped —
    a Guardian is the richer record and its template context (real child
    names) is strictly better.  Case-insensitive on the address."""
    rows = []
    seen_emails = set()

    for guardian in guardian_recipients(campaign):
        key = guardian.email.strip().lower()
        if key in seen_emails:
            continue
        seen_emails.add(key)
        rows.append(EmailCampaignRecipient(campaign=campaign, guardian=guardian, email=guardian.email))

    for lead in lead_recipients(campaign):
        key = lead.email.strip().lower()
        if not key or key in seen_emails:
            continue
        seen_emails.add(key)
        rows.append(EmailCampaignRecipient(campaign=campaign, lead=lead, email=lead.email))

    return rows


def compute_recipient_count(campaign):
    """Recipient count for the preview screen. Builds the same rows
    compute_recipient_rows would (so the de-dupe is reflected) and counts
    them — a preview action, not a hot path."""
    return len(compute_recipient_rows(campaign))


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
    """recipients: EmailCampaignRecipient queryset
    (select_related('guardian', 'lead') is the caller's job). Returns a list
    of Resend email dicts, same order as recipients, so a caller can zip()
    the response back onto each row."""
    payload = []
    for recipient in recipients:
        context = context_for_recipient(recipient)
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
    """Validates every currently-pending recipient's address *before* it's
    grouped into a batch, then splits the survivors into batches of
    RESEND_BATCH_MAX_SIZE and creates one Cloud Task per batch. This is the
    single choke point for that validation — every path that marks a row
    "pending" (send_campaign, retry_failed_recipients) always ends here
    before Resend is ever called, so a bad address can never sneak into a
    batch regardless of how the row got queued. Rejected rows are marked
    "skipped_invalid" immediately and never see a Cloud Task at all, rather
    than being sent to Resend just to fail there — this is the actual fix
    for the all-or-nothing batch problem: one bad address used to fail
    *every* recipient in its batch (confirmed directly), not just itself.

    Imports google.cloud.tasks_v2 lazily so the rest of this module
    (template rendering, recipient computation — all independently testable)
    doesn't require GCP credentials just to import.

    Returns (task_count, skipped_count)."""
    from google.cloud import tasks_v2

    pending = list(campaign.recipients.filter(status="pending").only("id", "email"))
    if not pending:
        return 0, 0

    invalid_rows = []
    valid_ids = []
    for recipient in pending:
        reason = invalid_email_reason(recipient.email)
        if reason:
            recipient.status = "skipped_invalid"
            recipient.error_message = reason
            invalid_rows.append(recipient)
        else:
            valid_ids.append(recipient.id)

    if invalid_rows:
        EmailCampaignRecipient.objects.bulk_update(invalid_rows, ["status", "error_message"])

    skipped_count = len(invalid_rows)
    if not valid_ids:
        finalize_campaign(campaign)
        return 0, skipped_count

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(settings.GCP_PROJECT_ID, settings.CLOUD_TASKS_LOCATION, settings.CLOUD_TASKS_QUEUE)
    url = f"{settings.FRONTEND_BASE_URL}/api/admissions/internal/send-campaign-batch/"

    task_count = 0
    for i in range(0, len(valid_ids), RESEND_BATCH_MAX_SIZE):
        batch_ids = valid_ids[i:i + RESEND_BATCH_MAX_SIZE]
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

    return task_count, skipped_count


def finalize_campaign(campaign):
    """Recomputes sent/failed/skipped/pending counts from the real
    EmailCampaignRecipient rows and, once none are left "pending", sets the
    campaign's final status. Shared by BulkEmailBatchSendView (called after
    each batch response) and enqueue_campaign_send (called directly when
    every pending recipient turned out to be invalid, so no Cloud Task was
    ever created to trigger the usual finalization) — a single place that
    decides "is this campaign done, and how did it go" rather than two
    copies that could drift apart."""
    counts = campaign.recipients.aggregate(
        sent=Count("id", filter=Q(status="sent")),
        failed=Count("id", filter=Q(status="failed")),
        skipped=Count("id", filter=Q(status="skipped_invalid")),
        pending=Count("id", filter=Q(status="pending")),
    )
    campaign.sent_count = counts["sent"]
    campaign.failed_count = counts["failed"]
    campaign.skipped_count = counts["skipped"]
    update_fields = ["sent_count", "failed_count", "skipped_count"]
    if counts["pending"] == 0:
        campaign.status = "sent" if counts["sent"] > 0 else "failed"
        campaign.sent_at = timezone.now()
        update_fields += ["status", "sent_at"]
    campaign.save(update_fields=update_fields)
