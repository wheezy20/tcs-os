from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import emails, storage
from .models import Offer
from .serializers import (
    ApplicationSerializer, InquirySerializer, OfferResponseSerializer, UploadURLRequestSerializer,
)


class InquiryCreateView(generics.CreateAPIView):
    """POST /api/admissions/inquiries/ — public endpoint, the entry point for
    a family enquiring for the first time. Everything else (review, stage
    changes, document approval) happens through Django admin for now."""
    serializer_class = InquirySerializer
    permission_classes = [permissions.AllowAny]

    def perform_create(self, serializer):
        family = serializer.save()
        emails.send_inquiry_emails(family, family.created_applications)


class ApplicationCreateView(generics.CreateAPIView):
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


class OfferRespondView(APIView):
    """POST /api/admissions/offers/<token>/respond/ — public, no parent
    portal exists so the offer's own unguessable token is the entire access
    control, same trust model as the public Inquiry/Application forms.
    Doesn't require the token to be linked to any authenticated identity."""
    permission_classes = [permissions.AllowAny]

    def post(self, request, token):
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
