from django.urls import path

from .views import (
    AccountCreateView, AccountListView, AccountUpdateView, ExpenseCreateView, ExpenseListView,
    ExpenseReceiptUploadURLView, JournalEntryDetailView, JournalEntryListView,
)

app_name = "finance"

urlpatterns = [
    path("accounts/", AccountListView.as_view(), name="account-list"),
    path("accounts/create/", AccountCreateView.as_view(), name="account-create"),
    path("accounts/<int:pk>/edit/", AccountUpdateView.as_view(), name="account-edit"),

    path("expenses/", ExpenseListView.as_view(), name="expense-list"),
    path("expenses/create/", ExpenseCreateView.as_view(), name="expense-create"),
    path("expenses/receipt-upload-url/", ExpenseReceiptUploadURLView.as_view(), name="expense-receipt-upload-url"),

    path("journal-entries/", JournalEntryListView.as_view(), name="journal-entry-list"),
    path("journal-entries/<str:pk>/", JournalEntryDetailView.as_view(), name="journal-entry-detail"),
]
