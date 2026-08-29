import hmac
import logging

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from . import bulk_email, emails, storage, turnstile
from .models import ApplicationDraft, EmailCampaign, EmailCampaignRecipient, Guardian, Offer
from .serializers import (
    ApplicationDraftSaveSerializer, ApplicationSerializer, InquirySerializer,
    OfferResponseSerializer, UploadURLRequestSerializer,
)

logger = logging.getLogger(__name__)


class DraftRateThrottle(AnonRateThrottle):
    """A separate, higher rate than the default "anon" scope — frequent
    saves are the whole point of autosaving on every section change (Phase 5
    free navigation), not something to guard against the way the real
    submission endpoints' tight default rate is meant to. See
    DRAFT_THROTTLE_RATE in settings.py."""
    scope = "application_draft"


def _turnstile_error_response(request):
    """None if the request's turnstile_token is valid, else a 400 Response
    shaped like a normal field error so the frontend's existing
    showFieldErrors() handles it with no special-casing."""
    try:
        turnstile.verify_turnstile_token(request.data.get("turnstile_token"), request.META.get("REMOTE_ADDR"))
    except turnstile.TurnstileVerificationError as exc:
        return Response({"turnstile_token": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
    return None


class TurnstileProtectedCreateMixin:
    """Verifies a Cloudflare Turnstile token before create() runs. Only on
    endpoints that create a real record — see turnstile.py's docstring for
    why the draft save endpoints are deliberately excluded."""

    def create(self, request, *args, **kwargs):
        error = _turnstile_error_response(request)
        if error:
            return error
        return super().create(request, *args, **kwargs)


class InquiryCreateView(TurnstileProtectedCreateMixin, generics.CreateAPIView):
    """POST /api/admissions/inquiries/ — public endpoint, the entry point for
    a family enquiring for the first time. Everything else (review, stage
    changes, document approval) happens through Django admin for now."""
    serializer_class = InquirySerializer
    permission_classes = [permissions.AllowAny]

    def perform_create(self, serializer):
        family = serializer.save()
        emails.send_inquiry_emails(family, family.created_applications)


class ApplicationCreateView(TurnstileProtectedCreateMixin, generics.CreateAPIView):
    """POST /api/admissions/applications/ — public endpoint, open to anyone
    (doesn't require a prior Inquiry). See ApplicationSerializer for the
    Family/Guardian/Student matching logic that avoids duplicate records."""
    serializer_class = ApplicationSerializer
    permission_classes = [permissions.AllowAny]

    def perform_create(self, serializer):
        application = serializer.save()
        emails.send_application_emails(application)


class UploadURLView(APIView):
    """POST /api/admissions/upload-url/ — public endpoint used by the
    Application form before the real submission. Mints a signed Supabase
    Storage upload URL for one file; the browser PUTs directly to Supabase
    afterwards, so the file itself never passes through this server."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = UploadURLRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        document_type = serializer.validated_data["document_type"]
        filename = serializer.validated_data["filename"]

        try:
            storage_path, upload_url = storage.create_upload_target(document_type, filename)
        except storage.SupabaseStorageError:
            return Response(
                {"detail": "Could not prepare the file upload. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"upload_url": upload_url, "file_path": storage_path})


class ApplicationDraftView(APIView):
    """POST /api/admissions/application-drafts/ — public endpoint, save the
    first bit of progress on the multi-step Application form and get back a
    token. Same trust model as everywhere else in this system: the token
    itself is the access control, no parent login exists. See
    ApplicationDraft's own docstring for why raw JSON is stored rather than
    partial real rows."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [DraftRateThrottle]

    def post(self, request):
        serializer = ApplicationDraftSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        draft = ApplicationDraft.objects.create(**serializer.validated_data)

        if request.data.get("send_email"):
            emails.send_draft_resume_email(draft)

        return Response(
            {"token": draft.token, "current_step": draft.current_step},
            status=status.HTTP_201_CREATED,
        )


class ApplicationDraftDetailView(APIView):
    """GET .../<token>/ — resume: fetch saved progress to repopulate the form.
    PATCH .../<token>/ — save progress against an existing draft (autosave
    between steps, or an explicit "save for later" click)."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [DraftRateThrottle]

    def get(self, request, token):
        draft = get_object_or_404(ApplicationDraft, token=token)
        if draft.is_submitted:
            return Response(
                {"detail": "This application has already been submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if draft.is_expired:
            return Response(
                {"detail": "This saved application has expired. Please start a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({
            "email": draft.email,
            "data": draft.data,
            "current_step": draft.current_step,
        })

    def patch(self, request, token):
        draft = get_object_or_404(ApplicationDraft, token=token)
        if draft.is_submitted:
            return Response(
                {"detail": "This application has already been submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if draft.is_expired:
            return Response(
                {"detail": "This saved application has expired. Please start a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ApplicationDraftSaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        for field, value in serializer.validated_data.items():
            setattr(draft, field, value)
        draft.save()

        if request.data.get("send_email"):
            emails.send_draft_resume_email(draft)

        return Response({"token": draft.token, "current_step": draft.current_step})


class ApplicationDraftSubmitView(APIView):
    """POST .../<token>/submit/ — final submission. Runs the whole assembled
    draft through the real ApplicationSerializer (the one and only place
    "valid" is defined), same as a direct POST to /applications/ would be."""
    permission_classes = [permissions.AllowAny]

    def post(self, request, token):
        error = _turnstile_error_response(request)
        if error:
            return error

        draft = get_object_or_404(ApplicationDraft, token=token)
        if draft.is_submitted:
            return Response(
                {"detail": "This application has already been submitted.",
                 "application_id": draft.submitted_application_id},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if draft.is_expired:
            return Response(
                {"detail": "This saved application has expired. Please start a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ApplicationSerializer(data=draft.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        application = serializer.save()
        emails.send_application_emails(application)

        draft.submitted_application = application
        draft.save(update_fields=["submitted_application"])

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class OfferRespondView(APIView):
    """POST /api/admissions/offers/<token>/respond/ — public, no parent
    portal exists so the offer's own unguessable token is the entire access
    control, same trust model as the public Inquiry/Application forms.
    Doesn't require the token to be linked to any authenticated identity."""
    permission_classes = [permissions.AllowAny]

    def post(self, request, token):
        error = _turnstile_error_response(request)
        if error:
            return error

        offer = get_object_or_404(Offer, token=token)
        offer.refresh_expiry()  # settle a lazily-detected expiry before deciding anything

        if offer.response != "pending":
            return Response(
                {"detail": f"This offer has already been resolved (status: {offer.response})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = OfferResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        offer.response = serializer.validated_data["response"]
        offer.responded_at = timezone.now()
        offer.save()

        return Response({
            "application_id": offer.application_id,
            "student_full_name": offer.application.student.full_name,
            "response": offer.response,
        })


@method_decorator(csrf_exempt, name="dispatch")
class UnsubscribeView(View):
    """Phase 6 — GET .../unsubscribe/<token>/ is for a human clicking the
    link in a bulk email's body; POST is the RFC 8058 one-click unsubscribe
    Gmail/Outlook fire directly (silently, no rendered response shown to the
    user) when someone clicks the mail client's own native Unsubscribe
    button — both do the same thing. csrf_exempt because RFC 8058's whole
    point is a mail provider's server posting with no browser session/CSRF
    cookie at all (this isn't a DRF APIView, which gets csrf_exempt
    automatically — see the CSRF investigation in docs/admissions/
    04-build-log.md for why that automatic exemption doesn't reach plain
    Django views). Safe here: unsubscribing is idempotent and low-stakes,
    and the token itself is the real access control, same trust model as
    Offer/ApplicationDraft.
    same trust model as Offer/ApplicationDraft. Only ever touches
    Guardian.bulk_email_unsubscribed_at — never checked by transactional
    email (emails.py), so this can't accidentally suppress an offer or
    confirmation email."""

    def _unsubscribe(self, token):
        guardian = get_object_or_404(Guardian, bulk_email_unsubscribe_token=token)
        if guardian.bulk_email_unsubscribed_at is None:
            guardian.bulk_email_unsubscribed_at = timezone.now()
            guardian.save(update_fields=["bulk_email_unsubscribed_at"])
        return guardian

    def get(self, request, token):
        guardian = self._unsubscribe(token)
        return render(request, "public/unsubscribed.html", {"guardian_first_name": guardian.first_name})

    def post(self, request, token):
        self._unsubscribe(token)
        return HttpResponse(status=200)


class BulkEmailBatchSendView(APIView):
    """Cloud Tasks' HTTP target for one batch (<=100 recipients) of a bulk
    email campaign — not a public API. Protected by a shared-secret header,
    not a DRF permission class, since "the caller is Cloud Tasks" isn't a
    concept DRF's permission model has; compared with hmac.compare_digest,
    not `==`, so a malformed/guessed secret can't be distinguished from a
    correct one by response-timing. Refuses everything if the secret isn't
    configured at all — an empty expected value must never accidentally
    open this endpoint to compare_digest("", "")."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        expected = settings.BULK_EMAIL_INTERNAL_SECRET
        provided = request.headers.get("X-Internal-Secret", "")
        if not expected or not hmac.compare_digest(provided, expected):
            return Response(status=status.HTTP_403_FORBIDDEN)

        campaign = get_object_or_404(EmailCampaign, id=request.data.get("campaign_id"))
        recipient_ids = request.data.get("recipient_ids") or []
        recipients = list(
            EmailCampaignRecipient.objects.filter(id__in=recipient_ids, status="pending")
            .select_related("guardian", "campaign")
        )
        if not recipients:
            bulk_email.finalize_campaign(campaign)
            return Response({"processed": 0})

        # Cloud Tasks includes this on a retried dispatch — 0 on the first
        # attempt. Only mark recipients permanently "failed" once no more
        # retries are coming; otherwise leave them "pending" so the next
        # retry (which re-queries by status="pending") picks them back up,
        # rather than being silently skipped forever.
        retry_count = int(request.headers.get("X-CloudTasks-TaskRetryCount", 0))
        is_last_attempt = retry_count >= settings.CLOUD_TASKS_MAX_ATTEMPTS - 1

        try:
            payload = bulk_email.build_batch_payload(recipients)
            result = bulk_email.send_resend_batch(payload)
            sent_ids = result.get("data", [])
            now = timezone.now()
            for recipient, item in zip(recipients, sent_ids):
                recipient.status = "sent"
                recipient.resend_message_id = item.get("id", "")
                recipient.sent_at = now
                recipient.save(update_fields=["status", "resend_message_id", "sent_at"])
        except bulk_email.ResendBatchError as exc:
            logger.warning("Bulk email batch send failed (campaign %s, attempt %s): %s", campaign.id, retry_count, exc)
            if not is_last_attempt:
                return Response({"detail": "transient failure, will retry"}, status=status.HTTP_502_BAD_GATEWAY)
            for recipient in recipients:
                recipient.status = "failed"
                recipient.error_message = str(exc)
                recipient.save(update_fields=["status", "error_message"])

        bulk_email.finalize_campaign(campaign)
        return Response({"processed": len(recipients)})
