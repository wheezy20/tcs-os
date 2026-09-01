from django.urls import path
from .views import (
    ApplicationCreateView, ApplicationDraftDetailView, ApplicationDraftSubmitView, ApplicationDraftView,
    BulkEmailBatchSendView, InquiryCreateView, OfferRespondView, PdfGateCreateView,
    QuickInterestCreateView, UnsubscribeView, UploadURLView,
)

urlpatterns = [
    path("inquiries/", InquiryCreateView.as_view(), name="inquiry-create"),
    path("applications/", ApplicationCreateView.as_view(), name="application-create"),
    path("quick-interest/", QuickInterestCreateView.as_view(), name="quick-interest-create"),
    path("pdf-gate/admissions-overview/", PdfGateCreateView.as_view(), name="pdf-gate-admissions-overview"),
    path("upload-url/", UploadURLView.as_view(), name="upload-url"),
    path("offers/<str:token>/respond/", OfferRespondView.as_view(), name="offer-respond"),
    path("application-drafts/", ApplicationDraftView.as_view(), name="application-draft-create"),
    path("application-drafts/<str:token>/", ApplicationDraftDetailView.as_view(), name="application-draft-detail"),
    path(
        "application-drafts/<str:token>/submit/",
        ApplicationDraftSubmitView.as_view(),
        name="application-draft-submit",
    ),
    path("unsubscribe/<str:token>/", UnsubscribeView.as_view(), name="bulk-email-unsubscribe"),
    path("internal/send-campaign-batch/", BulkEmailBatchSendView.as_view(), name="bulk-email-send-batch"),
]
