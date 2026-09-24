from django.urls import path

from .views import (
    PayrollRunApproveView, PayrollRunCreateView, PayrollRunDetailView,
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
]
