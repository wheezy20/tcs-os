"""Plain-text notification emails for Phase 2. One confirmation (to the
guardian) and one internal alert (to admissions staff) per submission event —
a multi-child Inquiry still sends exactly two emails, not two per child.

Sent via Django's EMAIL_BACKEND (console backend in dev — see settings.py).
A failure in the synchronous path (Offer, Lead capture) is logged and
swallowed rather than raised, so a broken mail config never blocks a real
submission.

Inquiry / Application / draft-resume emails go through a different path
(`enqueue_submission_emails` → a `TransactionalEmail` row → a Cloud Task →
`TransactionalEmailSendView`), so the submission's HTTP response doesn't wait
on the SMTP round trip. If enqueueing fails they fall back to sending inline,
so behaviour with no queue configured (dev, tests) is unchanged.
"""

import json
import logging
import mimetypes
import os
from contextlib import contextmanager

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

logger = logging.getLogger(__name__)


def _staff_email():
    return os.environ.get("ADMISSIONS_STAFF_EMAIL", "admissions@tcsch.edu.gh")


@contextmanager
def _shared_connection():
    """Opens one SMTP connection to send multiple emails for the same
    submission event, instead of each _send() call opening (and
    TLS-handshaking) its own — measured directly against the real Resend
    relay at ~2.5-3s per connection open, so a 2-email event (guardian
    confirmation + staff alert) was needlessly paying that twice. Falls back
    to per-email connections (yields None, _send()'s existing default
    behavior) if even opening the shared connection fails — email plumbing
    must never block the real submission, the same rule _send() already
    follows for individual sends."""
    connection = get_connection()
    try:
        connection.open()
        yield connection
    except Exception:
        logger.exception("Failed to open shared email connection, falling back to per-email connections")
        yield None
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _deliver(subject, message, recipient, attachments=None, connection=None):
    """Actually build and send one EmailMessage. Raises on any failure — the
    caller decides whether to swallow it (`_send`, for the synchronous
    Offer/Lead paths) or record it (`send_transactional_rows`, which needs to
    know per-row so Cloud Tasks can retry).

    attachments is a list of filenames resolved against
    settings.ADMISSIONS_ATTACHMENTS_DIR — generic on purpose. A missing file
    is logged and skipped (not an error), so a not-yet-uploaded prospectus
    never turns into a failed send. connection, when passed, is a
    caller-managed open SMTP connection (see _shared_connection) reused
    across calls in the same event."""
    email = EmailMessage(
        subject=subject,
        body=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        connection=connection,
    )
    for filename in attachments or []:
        path = os.path.join(settings.ADMISSIONS_ATTACHMENTS_DIR, filename)
        if not os.path.isfile(path):
            logger.warning("Email attachment not found, skipping: %s", path)
            continue
        content_type, _ = mimetypes.guess_type(path)
        with open(path, "rb") as f:
            email.attach(filename, f.read(), content_type)
    email.send()


def _send(subject, message, recipient, attachments=None, connection=None):
    """_deliver, with any failure logged and swallowed — the "never let email
    plumbing block the real submission" rule. Used by the synchronous senders
    (Offer, Lead capture)."""
    try:
        _deliver(subject, message, recipient, attachments, connection)
    except Exception:
        logger.exception("Failed to send admissions email: %s", subject)


# ---------------------------------------------------------------------------
# Transactional email — dispatched off the request/response cycle (b2).
#
# The submission view calls send_inquiry_emails / send_application_emails /
# send_draft_resume_email exactly as before; those now render each message
# into a TransactionalEmail row and enqueue one Cloud Task, returning at
# once. TransactionalEmailSendView (hit by the task) calls
# send_transactional_rows to actually deliver them. If the enqueue fails —
# no GCP creds locally, a transient Cloud Tasks error — the rows are sent
# inline right then, so the observable behaviour with no queue configured is
# identical to the old synchronous path.
# ---------------------------------------------------------------------------

def _build_inquiry_specs(family, applications):
    guardian = family.guardians.first()
    if not guardian or not applications:
        return []

    student_names = ", ".join(a.student.full_name for a in applications)
    reference_lines = "\n".join(
        f"  - {a.student.full_name}: {a.inquiry_reference or '(reference pending)'}"
        for a in applications
    )
    child_lines = "\n".join(
        f"  - {a.student.full_name} ({a.inquiry_reference}): {a.year_group_applied_for} "
        f"({a.academic_year}, {a.month_of_enrollment or 'month TBD'})"
        for a in applications
    )
    return [
        {
            "kind": "inquiry_parent",
            "to": guardian.email,
            "subject": "We've received your enquiry — TCS Admissions",
            "body": (
                f"Dear {guardian.first_name},\n\n"
                f"Thank you for your enquiry regarding {student_names}. Our admissions "
                "team has received your submission and will be in touch soon with next "
                f"steps.\n\nYour reference number(s):\n{reference_lines}\n\n"
                "— TCS Admissions"
            ),
            # Drop a prospectus/brochure into ADMISSIONS_ATTACHMENTS_DIR and
            # list its filename in settings.INQUIRY_EMAIL_ATTACHMENTS — no code
            # change. Resolved at send time, not now.
            "attachments": list(settings.INQUIRY_EMAIL_ATTACHMENTS),
        },
        {
            "kind": "inquiry_staff",
            "to": _staff_email(),
            "subject": f"New admissions enquiry — {student_names}",
            "body": (
                "A new enquiry was submitted.\n\n"
                f"Guardian: {guardian.full_name} <{guardian.email}> {guardian.phone}\n"
                f"Referral source: {family.get_referral_source_display() or 'n/a'}\n\n"
                f"Children:\n{child_lines}\n\n"
                f"Family #{family.pk} in Django admin."
            ),
        },
    ]


def _build_application_specs(application):
    student = application.student
    guardian = student.family.guardians.first()
    if not guardian:
        return []

    doc_lines = "\n".join(
        f"  - {d.get_document_type_display()}" for d in application.documents.all()
    ) or "  (none uploaded)"
    return [
        {
            "kind": "application_parent",
            "to": guardian.email,
            "subject": "We've received your application — TCS Admissions",
            "body": (
                f"Dear {guardian.first_name},\n\n"
                f"Thank you for submitting a formal application for {student.full_name} "
                f"({application.year_group_applied_for}, {application.academic_year}). "
                "Our admissions team has received your documents and will review your "
                f"application soon.\n\nYour reference number: {application.application_reference}\n\n"
                f"{settings.APPLICATION_FEE_PAYMENT_INSTRUCTIONS}\n\n"
                "— TCS Admissions"
            ),
        },
        {
            "kind": "application_staff",
            "to": _staff_email(),
            "subject": f"New admissions application — {student.full_name}",
            "body": (
                "A new formal application was submitted.\n\n"
                f"Reference: {application.application_reference}\n"
                f"Student: {student.full_name} — {application.year_group_applied_for} "
                f"({application.academic_year})\n"
                f"Guardian: {guardian.full_name} <{guardian.email}> {guardian.phone}\n\n"
                f"Documents:\n{doc_lines}\n\n"
                f"Application #{application.pk} in Django admin."
            ),
        },
    ]


def _build_draft_resume_specs(draft):
    if not draft.email:
        return []
    link = f"{settings.FRONTEND_BASE_URL}/apply?draft_token={draft.token}"
    expiry_note = (
        f"This saved application expires on {draft.expires_at:%d %B %Y}."
        if draft.expires_at else ""
    )
    return [{
        "kind": "draft_resume",
        "to": draft.email,
        "subject": "Resume your TCS application",
        "body": (
            "You saved your progress on a TCS application. Continue here whenever "
            f"you're ready: {link}\n\n"
            f"{expiry_note}\n\n"
            "— TCS Admissions"
        ),
    }]


def enqueue_submission_emails(specs, application=None):
    """Persist each spec as a pending TransactionalEmail and enqueue one Cloud
    Task to deliver them. On enqueue failure, send them inline right away
    (best-effort — same guarantee level as the old synchronous path).

    Assumes the rows are committed by the time the Cloud Task can hit the
    worker — true here because this project doesn't use ATOMIC_REQUESTS, so
    bulk_create() autocommits before enqueue. If ATOMIC_REQUESTS is ever
    enabled, this (and bulk_email.send_campaign) needs a transaction.on_commit
    hop so the task can't race ahead of the commit."""
    from .models import TransactionalEmail

    if not specs:
        return
    rows = TransactionalEmail.objects.bulk_create([
        TransactionalEmail(
            kind=s["kind"], to_email=s["to"], subject=s["subject"],
            body=s["body"], attachments=list(s.get("attachments") or []),
            application=application,
        )
        for s in specs
    ])
    ids = [r.id for r in rows]
    try:
        enqueue_transactional_ids(ids)
    except Exception:
        logger.exception("Transactional email enqueue failed; sending %d inline", len(ids))
        send_transactional_rows(
            TransactionalEmail.objects.filter(id__in=ids, status="pending"),
            is_last_attempt=True,
        )


def enqueue_transactional_ids(email_ids):
    """One Cloud Task -> POST /api/admissions/internal/send-transactional-email/
    with the row ids. Shares BULK_EMAIL_INTERNAL_SECRET / X-Internal-Secret
    with the bulk-email internal endpoint (see internal_auth.py). Lazy import
    of google.cloud.tasks_v2 so the rest of this module stays importable and
    testable without GCP libs/creds."""
    email_ids = list(email_ids)
    if not email_ids:
        return
    if not settings.GCP_PROJECT_ID:
        # No queue configured (local dev, tests, or a misconfigured deploy) —
        # fail fast so enqueue_submission_emails falls straight through to its
        # inline send instead of paying a multi-second gRPC/credential timeout
        # only to fail anyway.
        raise RuntimeError("GCP_PROJECT_ID unset — Cloud Tasks not configured")

    from google.cloud import tasks_v2

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(
        settings.GCP_PROJECT_ID, settings.CLOUD_TASKS_LOCATION,
        settings.CLOUD_TASKS_TRANSACTIONAL_QUEUE,
    )
    url = f"{settings.FRONTEND_BASE_URL}/api/admissions/internal/send-transactional-email/"
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {
                "Content-Type": "application/json",
                "X-Internal-Secret": settings.BULK_EMAIL_INTERNAL_SECRET,
            },
            "body": json.dumps({"email_ids": email_ids}).encode(),
        }
    }
    client.create_task(request={"parent": parent, "task": task})


def send_transactional_rows(rows, *, is_last_attempt):
    """Deliver each still-`pending` TransactionalEmail over one shared SMTP
    connection. A row already `sent` is skipped (idempotent — safe for a
    Cloud Tasks redelivery). On failure: bump `attempts`, and mark `failed`
    with the error only if no retry is coming (`is_last_attempt`), otherwise
    leave it `pending` so the next attempt picks it up. Returns the count
    actually sent this pass."""
    from .models import TransactionalEmail

    rows = [r for r in (rows if isinstance(rows, list) else list(rows)) if r.status == "pending"]
    if not rows:
        return 0

    sent = 0
    with _shared_connection() as connection:
        for row in rows:
            row.attempts += 1
            try:
                _deliver(row.subject, row.body, row.to_email,
                         attachments=row.attachments, connection=connection)
            except Exception as exc:
                logger.warning(
                    "Transactional email %s (%s) failed on attempt %s: %s",
                    row.id, row.kind, row.attempts, exc,
                )
                if is_last_attempt:
                    row.status = "failed"
                    row.last_error = str(exc)[:2000]
                row.save(update_fields=["attempts", "status", "last_error"])
                continue
            row.status = "sent"
            row.sent_at = timezone.now()
            row.save(update_fields=["attempts", "status", "sent_at"])
            sent += 1
    return sent


# Public API the submission views call — unchanged names/signatures, now
# non-blocking.

def send_inquiry_emails(family, applications):
    enqueue_submission_emails(
        _build_inquiry_specs(family, applications),
        application=applications[0] if applications else None,
    )


def send_application_emails(application):
    enqueue_submission_emails(_build_application_specs(application), application=application)


def send_draft_resume_email(draft):
    """Sent on explicit request (a "save for later" click), not on every
    autosave — an email per keystroke-level save would spam the parent's
    inbox. Same token-is-the-access-control trust model as Offer's resume
    link; see ApplicationDraft's docstring."""
    enqueue_submission_emails(_build_draft_resume_specs(draft))


def send_offer_email(offer):
    """Sent to the parent when staff generate an Offer. No parent portal
    exists, so the accept/decline link carries the offer's own unguessable
    token — same trust model as the public Inquiry/Application forms."""
    application = offer.application
    student = application.student
    guardian = student.family.guardians.first()
    if not guardian:
        return

    link = f"{settings.FRONTEND_BASE_URL}/offer?token={offer.token}"
    expiry_note = (
        f"This offer expires on {offer.expires_at:%d %B %Y}."
        if offer.expires_at else ""
    )

    _send(
        subject=f"You have an offer from TCS — {student.full_name}",
        message=(
            f"Dear {guardian.first_name},\n\n"
            f"We're pleased to offer {student.full_name} a place at TCS for "
            f"{application.year_group_applied_for} ({application.academic_year}).\n\n"
            f"Please respond here: {link}\n\n"
            f"{expiry_note}\n\n"
            "If you'd rather confirm by phone, please call our admissions office.\n\n"
            "— TCS Admissions"
        ),
        recipient=guardian.email,
    )


def _lead_staff_alert(lead, headline, connection=None):
    consent = "yes" if lead.consent_to_marketing else "no"
    _send(
        subject=f"{headline} — {lead.name}",
        message=(
            f"{headline}.\n\n"
            f"Name: {lead.name}\n"
            f"Email: {lead.email or '(none)'}\n"
            f"Phone: {lead.phone or '(none)'}\n"
            f"Grade of interest: {lead.grade_interest or '(none)'}\n"
            f"Marketing opt-in: {consent}\n"
            f"Source: {lead.get_source_display()}\n\n"
            f"Lead #{lead.pk} in Django admin."
        ),
        recipient=_staff_email(),
        connection=connection,
    )


def send_quick_interest_email(lead):
    """Marketing-site quick-interest widget — staff notification only. No
    email to the lead: the widget just says "we'll be in touch"."""
    _lead_staff_alert(lead, "New quick-interest lead")


def send_pdf_gate_email(lead):
    """Gated "Admissions Overview & Fees" download. Emails the document to
    the lead (settings.PDF_GATE_ATTACHMENTS, resolved in
    ADMISSIONS_ATTACHMENTS_DIR — a missing file is logged and skipped, the
    lead is still captured), then notifies staff — both on one shared
    connection. lead.email is guaranteed present here (PdfGateSerializer
    requires it)."""
    with _shared_connection() as connection:
        _send(
            subject="Your TCS Admissions Overview & Fees",
            message=(
                f"Dear {lead.first_name or 'parent'},\n\n"
                "Thank you for your interest in Treasures Christian School. Our "
                "Admissions Overview & Fees document is attached.\n\n"
                "If you'd like to take the next step, you can start an enquiry at "
                f"{settings.FRONTEND_BASE_URL}/inquiry\n\n"
                "— TCS Admissions"
            ),
            recipient=lead.email,
            attachments=settings.PDF_GATE_ATTACHMENTS,
            connection=connection,
        )
        _lead_staff_alert(lead, "New PDF-gate lead (Admissions Overview & Fees)", connection=connection)
