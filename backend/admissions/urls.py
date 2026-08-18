from django.urls import path
from .views import ApplicationCreateView, InquiryCreateView, UploadURLView

urlpatterns = [
    path("inquiries/", InquiryCreateView.as_view(), name="inquiry-create"),
    path("applications/", ApplicationCreateView.as_view(), name="application-create"),
    path("upload-url/", UploadURLView.as_view(), name="upload-url"),
]
