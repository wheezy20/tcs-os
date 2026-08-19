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

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)


def _staff_email():
    return os.environ.get("ADMISSIONS_STAFF_EMAIL", "admissions@tcsch.edu.gh")


def _send(subject, message, recipient, attachments=None):
    """attachments is a list of filenames, resolved against
    settings.ADMISSIONS_ATTACHMENTS_DIR — generic on purpose, not tied to any
    one document, so a prospectus/brochure/whatever can be attached to any
    email just by naming it in a settings list, no code change needed. A
    missing file is logged and skipped, not a send failure — the same
    "never let email plumbing block the actual submission" rule as the rest
    of this module."""
    try:
        email = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
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
    )


def send_application_emails(application):
    student = application.student
    guardian = student.family.guardians.first()
    if not guardian:
        return

    _send(
        subject="We've received your application — TCS Admissions",
        message=(
            f"Dear {guardian.first_name},\n\n"
            f"Thank you for submitting a formal application for {student.full_name} "
            f"({application.year_group_applied_for}, {application.academic_year}). "
            "Our admissions team has received your documents and will review your "
            f"application soon.\n\nYour reference number: {application.application_reference}\n\n"
            "— TCS Admissions"
        ),
        recipient=guardian.email,
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

    link = f"{settings.FRONTEND_BASE_URL}/offer.html?token={offer.token}"
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
