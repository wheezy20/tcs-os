from rest_framework import generics, permissions
from .serializers import InquirySerializer


class InquiryCreateView(generics.CreateAPIView):
    """POST /api/admissions/inquiries/ — public endpoint, the only one the
    frontend form talks to in Phase 1. Everything else (review, stage changes,
    document approval) happens through Django admin for now."""
    serializer_class = InquirySerializer
    permission_classes = [permissions.AllowAny]
