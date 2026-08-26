"""Plain-text notification emails for Phase 2. One confirmation (to the
guardian) and one internal alert (to admissions staff) per submission event —
a multi-child Inquiry still sends exactly two emails, not two per child.

Sent via Django's EMAIL_BACKEND (console backend in dev — see settings.py).
A failure here is logged and swallowed rather than raised, so a broken mail
config never blocks a real inquiry/application from being saved.
"""

import logging
import mimetypes
import os
from contextlib import contextmanager

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

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


def _send(subject, message, recipient, attachments=None, connection=None):
    """attachments is a list of filenames, resolved against
    settings.ADMISSIONS_ATTACHMENTS_DIR — generic on purpose, not tied to any
    one document, so a prospectus/brochure/whatever can be attached to any
    email just by naming it in a settings list, no code change needed. A
    missing file is logged and skipped, not a send failure — the same
    "never let email plumbing block the actual submission" rule as the rest
    of this module.

    connection, when passed, is a caller-managed open SMTP connection (see
    _shared_connection) reused across multiple _send() calls in the same
    submission event; left as None, EmailMessage opens (and closes) its own
    connection per call as before."""
    try:
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
    except Exception:
        logger.exception("Failed to send admissions email: %s", subject)


def send_inquiry_emails(family, applications):
    guardian = family.guardians.first()
    if not guardian or not applications:
        return

    student_names = ", ".join(a.student.full_name for a in applications)
    reference_lines = "\n".join(
        f"  - {a.student.full_name}: {a.inquiry_reference or '(reference pending)'}"
        for a in applications
    )

    with _shared_connection() as connection:
        _send(
            subject="We've received your enquiry — TCS Admissions",
            message=(
                f"Dear {guardian.first_name},\n\n"
                f"Thank you for your enquiry regarding {student_names}. Our admissions "
                "team has received your submission and will be in touch soon with next "
                f"steps.\n\nYour reference number(s):\n{reference_lines}\n\n"
                "— TCS Admissions"
            ),
            recipient=guardian.email,
            # Empty by default — see settings.INQUIRY_EMAIL_ATTACHMENTS. Drop a
            # prospectus/brochure into ADMISSIONS_ATTACHMENTS_DIR and list its
            # filename there to start attaching it, no code change needed.
            attachments=settings.INQUIRY_EMAIL_ATTACHMENTS,
            connection=connection,
        )

        child_lines = "\n".join(
            f"  - {a.student.full_name} ({a.inquiry_reference}): {a.year_group_applied_for} "
            f"({a.academic_year}, {a.month_of_enrollment or 'month TBD'})"
            for a in applications
        )
        _send(
            subject=f"New admissions enquiry — {student_names}",
            message=(
                "A new enquiry was submitted.\n\n"
                f"Guardian: {guardian.full_name} <{guardian.email}> {guardian.phone}\n"
                f"Referral source: {family.get_referral_source_display() or 'n/a'}\n\n"
                f"Children:\n{child_lines}\n\n"
                f"Family #{family.pk} in Django admin."
            ),
            recipient=_staff_email(),
            connection=connection,
        )


def send_application_emails(application):
    student = application.student
    guardian = student.family.guardians.first()
    if not guardian:
        return

    with _shared_connection() as connection:
        _send(
            subject="We've received your application — TCS Admissions",
            message=(
                f"Dear {guardian.first_name},\n\n"
                f"Thank you for submitting a formal application for {student.full_name} "
                f"({application.year_group_applied_for}, {application.academic_year}). "
                "Our admissions team has received your documents and will review your "
                f"application soon.\n\nYour reference number: {application.application_reference}\n\n"
                f"{settings.APPLICATION_FEE_PAYMENT_INSTRUCTIONS}\n\n"
                "— TCS Admissions"
            ),
            recipient=guardian.email,
            connection=connection,
        )

        doc_lines = "\n".join(
            f"  - {d.get_document_type_display()}" for d in application.documents.all()
        ) or "  (none uploaded)"
        _send(
            subject=f"New admissions application — {student.full_name}",
            message=(
                "A new formal application was submitted.\n\n"
                f"Reference: {application.application_reference}\n"
                f"Student: {student.full_name} — {application.year_group_applied_for} "
                f"({application.academic_year})\n"
                f"Guardian: {guardian.full_name} <{guardian.email}> {guardian.phone}\n\n"
                f"Documents:\n{doc_lines}\n\n"
                f"Application #{application.pk} in Django admin."
            ),
            recipient=_staff_email(),
            connection=connection,
        )


def send_draft_resume_email(draft):
    """Sent on explicit request (a "save for later" action on the multi-step
    Application form), not on every autosave — an email per keystroke-level
    save would spam the parent's inbox. Same token-is-the-access-control
    trust model as Offer's resume link; see ApplicationDraft's docstring."""
    if not draft.email:
        return

    link = f"{settings.FRONTEND_BASE_URL}/apply?draft_token={draft.token}"
    expiry_note = (
        f"This saved application expires on {draft.expires_at:%d %B %Y}."
        if draft.expires_at else ""
    )

    _send(
        subject="Resume your TCS application",
        message=(
            "You saved your progress on a TCS application. Continue here whenever "
            f"you're ready: {link}\n\n"
            f"{expiry_note}\n\n"
            "— TCS Admissions"
        ),
        recipient=draft.email,
    )


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
