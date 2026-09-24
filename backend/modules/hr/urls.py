from django.urls import path

from .views import (
    EmployeeDetailView, EmployeeDocumentGenerateView, EmployeeGeneratedDocumentDiscardView,
    EmployeeGeneratedDocumentIssueView, EmployeeGeneratedDocumentRecordAcceptanceView,
    EmployeeGeneratedDocumentViewLinkView, PayrollRunApproveView, PayrollRunCreateView, PayrollRunDetailView,
    PayrollRunGeneratePayslipsView, PayrollRunRejectView, PayrollRunSubmitView,
)

app_name = "hr"

urlpatterns = [
    path("payroll-runs/create/", PayrollRunCreateView.as_view(), name="payroll-run-create"),
    path("payroll-runs/<int:pk>/", PayrollRunDetailView.as_view(), name="payroll-run-detail"),
    path("payroll-runs/<int:pk>/generate/", PayrollRunGeneratePayslipsView.as_view(), name="payroll-run-generate"),
    path("payroll-runs/<int:pk>/submit/", PayrollRunSubmitView.as_view(), name="payroll-run-submit"),
    path("payroll-runs/<int:pk>/approve/", PayrollRunApproveView.as_view(), name="payroll-run-approve"),
    path("payroll-runs/<int:pk>/reject/", PayrollRunRejectView.as_view(), name="payroll-run-reject"),

    # Session 7 — employee-generated documents.
    path("employees/<int:pk>/", EmployeeDetailView.as_view(), name="employee-detail"),
    path(
        "employees/<int:pk>/documents/generate/",
        EmployeeDocumentGenerateView.as_view(), name="employee-document-generate",
    ),
    path(
        "documents/<int:pk>/issue/",
        EmployeeGeneratedDocumentIssueView.as_view(), name="employee-document-issue",
    ),
    path(
        "documents/<int:pk>/discard/",
        EmployeeGeneratedDocumentDiscardView.as_view(), name="employee-document-discard",
    ),
    path(
        "documents/<int:pk>/accept/",
        EmployeeGeneratedDocumentRecordAcceptanceView.as_view(), name="employee-document-accept",
    ),
    path(
        "documents/<int:pk>/view/",
        EmployeeGeneratedDocumentViewLinkView.as_view(), name="employee-document-view",
    ),
]
