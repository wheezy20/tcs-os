from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import emails, storage
from .serializers import ApplicationSerializer, InquirySerializer, UploadURLRequestSerializer


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
