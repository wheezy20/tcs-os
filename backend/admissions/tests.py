"""End-to-end coverage for the Lead capture feature: the two public
endpoints (quick-interest widget, PDF gate), Turnstile gating, the
consent-default safety net, bulk-email audience targeting, unsubscribe for
both Guardians and Leads, and the {{recipient_*}} placeholder aliases.

Turnstile is patched out (it makes a real Cloudflare HTTP call); email uses
Django's in-memory backend, so mail.outbox is asserted directly.
"""

import io
import os
import tempfile
from unittest import mock

from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient

from . import access, bulk_email, emails
from .admin import ApplicationAdmin, DocumentInline, FamilyAdmin, GuardianAdmin, HealthInfoInline, StudentAdmin
from .models import (
    Application, Campus, EmailCampaign, EmailCampaignRecipient, Family, Guardian, Lead, StaffProfile,
    Student, TransactionalEmail,
)

QUICK_INTEREST_URL = "/api/admissions/quick-interest/"
PDF_GATE_URL = "/api/admissions/pdf-gate/admissions-overview/"
INQUIRY_URL = "/api/admissions/inquiries/"
TRANSACTIONAL_SEND_URL = "/api/admissions/internal/send-transactional-email/"

_TURNSTILE_OK = mock.patch("admissions.turnstile.verify_turnstile_token", return_value=None)


class _PublicEndpointBase(TestCase):
    def setUp(self):
        cache.clear()  # AnonRateThrottle state lives in the default LocMemCache
        self.client = APIClient()
        self._t = _TURNSTILE_OK.start()
        self.addCleanup(_TURNSTILE_OK.stop)


class QuickInterestEndpointTests(_PublicEndpointBase):
    def test_minimal_success_with_email_only(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "Ama Mensah",
            "email": "ama@example-domain.gh",
            "turnstile_token": "x",
        }, format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(set(resp.data), {"id", "source", "created_at"})
        self.assertEqual(resp.data["source"], "quick_interest_widget")

        lead = Lead.objects.get(pk=resp.data["id"])
        self.assertEqual(lead.source, "quick_interest_widget")
        self.assertEqual(lead.name, "Ama Mensah")
        self.assertFalse(lead.consent_to_marketing)  # default safety net
        self.assertTrue(lead.bulk_email_unsubscribe_token)

        # Staff notification only — nothing sent to the lead.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("quick-interest", mail.outbox[0].subject.lower())

    def test_phone_only_is_accepted_and_validated(self):
        ok = self.client.post(QUICK_INTEREST_URL, {
            "name": "Kofi", "phone": "+233551794822", "turnstile_token": "x",
        }, format="json")
        self.assertEqual(ok.status_code, 201)

        bad = self.client.post(QUICK_INTEREST_URL, {
            "name": "Kofi", "phone": "0551794822", "turnstile_token": "x",
        }, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("phone", bad.data)

    def test_missing_both_email_and_phone_is_400(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "No Contact", "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("non_field_errors", resp.data)

    def test_consent_true_is_honoured_when_explicitly_sent(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "Opt In", "email": "optin@example-domain.gh",
            "consent_to_marketing": True, "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Lead.objects.get(pk=resp.data["id"]).consent_to_marketing)

    def test_source_from_client_is_ignored(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "Sneaky", "email": "s@example-domain.gh",
            "source": "pdf_gate_admissions_overview", "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Lead.objects.get(pk=resp.data["id"]).source, "quick_interest_widget")

    def test_bad_turnstile_token_is_400(self):
        from admissions import turnstile
        with mock.patch(
            "admissions.turnstile.verify_turnstile_token",
            side_effect=turnstile.TurnstileVerificationError("nope"),
        ):
            resp = self.client.post(QUICK_INTEREST_URL, {
                "name": "Bot", "email": "bot@example-domain.gh", "turnstile_token": "bad",
            }, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("turnstile_token", resp.data)
        self.assertEqual(Lead.objects.count(), 0)


class PdfGateEndpointTests(_PublicEndpointBase):
    def test_success_emails_lead_and_staff(self):
        resp = self.client.post(PDF_GATE_URL, {
            "name": "Yaa", "email": "yaa@example-domain.gh", "turnstile_token": "x",
        }, format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["source"], "pdf_gate_admissions_overview")
        self.assertIn("detail", resp.data)

        lead = Lead.objects.get(pk=resp.data["id"])
        self.assertEqual(lead.source, "pdf_gate_admissions_overview")
        self.assertFalse(lead.consent_to_marketing)

        # One to the lead (the document), one to staff.
        self.assertEqual(len(mail.outbox), 2)
        to_lead = [m for m in mail.outbox if m.to == ["yaa@example-domain.gh"]]
        self.assertEqual(len(to_lead), 1)

    def test_email_is_required(self):
        resp = self.client.post(PDF_GATE_URL, {
            "name": "No Email", "phone": "+233551794822", "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("email", resp.data)

    def test_consent_defaults_false_even_if_field_omitted(self):
        resp = self.client.post(PDF_GATE_URL, {
            "name": "Default", "email": "d@example-domain.gh", "turnstile_token": "x",
        }, format="json")
        self.assertFalse(Lead.objects.get(pk=resp.data["id"]).consent_to_marketing)

    def test_missing_pdf_file_is_skipped_not_fatal(self):
        # Point at an empty dir so the configured attachment filename resolves
        # to nothing — the real file now ships in the repo, so we can't rely on
        # it simply being absent (as this test originally did).
        with tempfile.TemporaryDirectory() as d:
            with override_settings(ADMISSIONS_ATTACHMENTS_DIR=d):
                resp = self.client.post(PDF_GATE_URL, {
                    "name": "Yaw", "email": "yaw@example-domain.gh", "turnstile_token": "x",
                }, format="json")
        self.assertEqual(resp.status_code, 201)
        to_lead = [m for m in mail.outbox if m.to == ["yaw@example-domain.gh"]][0]
        self.assertEqual(to_lead.attachments, [])  # nothing attached, still delivered

    def test_pdf_file_is_attached_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "admissions-overview-and-fees.pdf"), "wb") as f:
                f.write(b"%PDF-1.4 fake")
            with override_settings(ADMISSIONS_ATTACHMENTS_DIR=d):
                resp = self.client.post(PDF_GATE_URL, {
                    "name": "Abena", "email": "abena@example-domain.gh", "turnstile_token": "x",
                }, format="json")
        self.assertEqual(resp.status_code, 201)
        to_lead = [m for m in mail.outbox if m.to == ["abena@example-domain.gh"]][0]
        self.assertEqual(len(to_lead.attachments), 1)
        self.assertEqual(to_lead.attachments[0][0], "admissions-overview-and-fees.pdf")


class LeadUtmCaptureTests(_PublicEndpointBase):
    """b4 — the flat utm_source/utm_medium/utm_campaign fields, passed through
    in the POST body by the marketing-site form's JS."""

    def test_utm_params_captured_on_quick_interest(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "Ama", "email": "ama@example-domain.gh",
            "utm_source": "google", "utm_medium": "cpc", "utm_campaign": "spring-open-day-2026",
            "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        lead = Lead.objects.get(pk=resp.data["id"])
        self.assertEqual(
            (lead.utm_source, lead.utm_medium, lead.utm_campaign),
            ("google", "cpc", "spring-open-day-2026"),
        )

    def test_utm_params_captured_on_pdf_gate(self):
        resp = self.client.post(PDF_GATE_URL, {
            "name": "Yaa", "email": "yaa@example-domain.gh",
            "utm_source": "facebook", "utm_medium": "social", "utm_campaign": "fees-guide",
            "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        lead = Lead.objects.get(pk=resp.data["id"])
        self.assertEqual(lead.utm_source, "facebook")
        self.assertEqual(lead.utm_campaign, "fees-guide")

    def test_utm_absent_defaults_to_blank(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "No UTM", "email": "no-utm@example-domain.gh", "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        lead = Lead.objects.get(pk=resp.data["id"])
        self.assertEqual((lead.utm_source, lead.utm_medium, lead.utm_campaign), ("", "", ""))

    def test_utm_not_echoed_in_response(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "Ama", "email": "ama@example-domain.gh",
            "utm_source": "google", "turnstile_token": "x",
        }, format="json")
        self.assertEqual(set(resp.data), {"id", "source", "created_at"})

    def test_overlong_utm_is_truncated_not_rejected(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "Long", "email": "long@example-domain.gh",
            "utm_campaign": "x" * 500, "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 201)  # a junk-length UTM never costs a lead
        self.assertEqual(len(Lead.objects.get(pk=resp.data["id"]).utm_campaign), 200)

    def test_null_utm_value_is_accepted(self):
        resp = self.client.post(QUICK_INTEREST_URL, {
            "name": "Null", "email": "null@example-domain.gh",
            "utm_source": None, "utm_medium": None, "utm_campaign": None,
            "turnstile_token": "x",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Lead.objects.get(pk=resp.data["id"]).utm_source, "")


class BulkEmailAudienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.family = Family.objects.create()
        cls.guardian = Guardian.objects.create(
            family=cls.family, first_name="Gail", surname="Guardian",
            email="guardian@example-domain.gh", phone="+233551794820", relationship="mother",
        )
        cls.lead_ok = Lead.objects.create(
            name="Opted In", email="optin@example-domain.gh",
            source="quick_interest_widget", consent_to_marketing=True,
        )
        cls.lead_pdf = Lead.objects.create(
            name="Pdf Lead", email="pdf@example-domain.gh",
            source="pdf_gate_admissions_overview", consent_to_marketing=True,
        )
        cls.lead_no_consent = Lead.objects.create(
            name="No Consent", email="noconsent@example-domain.gh",
            source="quick_interest_widget", consent_to_marketing=False,
        )
        cls.lead_unsub = Lead.objects.create(
            name="Unsubbed", email="unsub@example-domain.gh",
            source="quick_interest_widget", consent_to_marketing=True,
        )
        cls.lead_unsub.bulk_email_unsubscribed_at = "2026-01-01T00:00:00Z"
        cls.lead_unsub.save(update_fields=["bulk_email_unsubscribed_at"])

    def _campaign(self, **kw):
        kw.setdefault("name", "C")
        kw.setdefault("subject", "S")
        kw.setdefault("body", "Hi {{recipient_first_name}} {{unsubscribe_link}}")
        return EmailCampaign.objects.create(**kw)

    def test_audience_guardians_only(self):
        rows = bulk_email.compute_recipient_rows(self._campaign(audience="guardians"))
        self.assertEqual([r.email for r in rows], ["guardian@example-domain.gh"])
        self.assertTrue(all(r.guardian_id and not r.lead_id for r in rows))

    def test_audience_leads_only_respects_consent_and_unsub(self):
        rows = bulk_email.compute_recipient_rows(self._campaign(audience="leads"))
        self.assertEqual(
            sorted(r.email for r in rows),
            ["optin@example-domain.gh", "pdf@example-domain.gh"],
        )
        self.assertTrue(all(r.lead_id and not r.guardian_id for r in rows))

    def test_audience_leads_filtered_by_source(self):
        rows = bulk_email.compute_recipient_rows(
            self._campaign(audience="leads", filter_lead_source="pdf_gate_admissions_overview")
        )
        self.assertEqual([r.email for r in rows], ["pdf@example-domain.gh"])

    def test_audience_both_unions(self):
        rows = bulk_email.compute_recipient_rows(self._campaign(audience="both"))
        self.assertEqual(
            sorted(r.email for r in rows),
            ["guardian@example-domain.gh", "optin@example-domain.gh", "pdf@example-domain.gh"],
        )

    def test_audience_both_dedupes_email_guardian_wins(self):
        Lead.objects.create(
            name="Also A Guardian", email="guardian@example-domain.gh",
            source="quick_interest_widget", consent_to_marketing=True,
        )
        rows = bulk_email.compute_recipient_rows(self._campaign(audience="both"))
        dupes = [r for r in rows if r.email == "guardian@example-domain.gh"]
        self.assertEqual(len(dupes), 1)
        self.assertTrue(dupes[0].guardian_id and not dupes[0].lead_id)

    def test_recipient_rows_persist_with_check_constraint(self):
        campaign = self._campaign(audience="both")
        rows = bulk_email.compute_recipient_rows(campaign)
        EmailCampaignRecipient.objects.bulk_create(rows)  # would raise if CHECK violated
        saved = EmailCampaignRecipient.objects.filter(campaign=campaign)
        self.assertEqual(saved.count(), 3)
        for r in saved:
            self.assertEqual(bool(r.guardian_id) ^ bool(r.lead_id), True)

    def test_batch_payload_renders_for_mixed_guardian_and_lead(self):
        campaign = self._campaign(
            audience="both",
            subject="Hello {{recipient_first_name}}",
            body="Dear {{recipient_full_name}} — {{student_names}} {{unsubscribe_link}}",
        )
        EmailCampaignRecipient.objects.bulk_create(bulk_email.compute_recipient_rows(campaign))
        rows = list(
            EmailCampaignRecipient.objects.filter(campaign=campaign)
            .select_related("guardian", "lead")
        )
        payload = bulk_email.build_batch_payload(rows)
        self.assertEqual(len(payload), 3)
        by_to = {p["to"][0]: p for p in payload}
        # Guardian recipient: real name, {{student_names}} resolves (empty family → fallback)
        g = by_to["guardian@example-domain.gh"]
        self.assertEqual(g["subject"], "Hello Gail")
        self.assertIn("your child", g["text"])
        # Lead recipient: name from the single field, lead's own unsubscribe token
        lead_row = next(r for r in rows if r.lead_id and r.email == "optin@example-domain.gh")
        self.assertIn(lead_row.lead.bulk_email_unsubscribe_token, by_to["optin@example-domain.gh"]["text"])


class PlaceholderAliasTests(TestCase):
    def test_guardian_context_has_recipient_and_guardian_keys(self):
        family = Family.objects.create()
        g = Guardian.objects.create(
            family=family, first_name="Kw", surname="Owusu",
            email="g@example-domain.gh", phone="+233551794820", relationship="father",
        )
        ctx = bulk_email.build_placeholder_context(g)
        self.assertEqual(ctx["recipient_first_name"], "Kw")
        self.assertEqual(ctx["guardian_first_name"], "Kw")
        self.assertEqual(ctx["recipient_full_name"], "Kw Owusu")
        self.assertEqual(bulk_email.render_template("Hi {{recipient_first_name}}", ctx), "Hi Kw")

    def test_lead_context_mirrors_keys_with_fallbacks(self):
        lead = Lead.objects.create(
            name="Esi Boateng", email="esi@example-domain.gh",
            source="quick_interest_widget", consent_to_marketing=True,
        )
        ctx = bulk_email.build_lead_placeholder_context(lead)
        self.assertEqual(ctx["recipient_first_name"], "Esi")
        self.assertEqual(ctx["guardian_first_name"], "Esi")  # back-compat alias
        self.assertEqual(ctx["student_names"], "your child")
        self.assertIn(lead.bulk_email_unsubscribe_token, ctx["unsubscribe_link"])


class UnsubscribeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.family = Family.objects.create()
        self.guardian = Guardian.objects.create(
            family=self.family, first_name="Gee", surname="Gee",
            email="g@example-domain.gh", phone="+233551794820", relationship="guardian",
        )
        self.lead = Lead.objects.create(
            name="Lena Lead", email="lena@example-domain.gh",
            source="quick_interest_widget", consent_to_marketing=True,
        )

    def _url(self, token):
        return f"/api/admissions/unsubscribe/{token}/"

    def test_guardian_token_unsubscribes_guardian(self):
        resp = self.client.get(self._url(self.guardian.bulk_email_unsubscribe_token))
        self.assertEqual(resp.status_code, 200)
        self.guardian.refresh_from_db()
        self.assertIsNotNone(self.guardian.bulk_email_unsubscribed_at)

    def test_lead_token_unsubscribes_lead(self):
        resp = self.client.get(self._url(self.lead.bulk_email_unsubscribe_token))
        self.assertEqual(resp.status_code, 200)
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.bulk_email_unsubscribed_at)

    def test_lead_one_click_post_unsubscribes_lead(self):
        resp = self.client.post(self._url(self.lead.bulk_email_unsubscribe_token))
        self.assertEqual(resp.status_code, 200)
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.bulk_email_unsubscribed_at)

    def test_unknown_token_is_404(self):
        self.assertEqual(self.client.get(self._url("no-such-token")).status_code, 404)

    def test_unsubscribed_lead_excluded_from_campaign(self):
        self.client.post(self._url(self.lead.bulk_email_unsubscribe_token))
        campaign = EmailCampaign.objects.create(
            name="C", subject="S", body="Hi {{recipient_first_name}} {{unsubscribe_link}}",
            audience="leads",
        )
        rows = bulk_email.compute_recipient_rows(campaign)
        self.assertEqual(rows, [])


@override_settings(GCP_PROJECT_ID="")  # force the inline-send path (b2), deterministic + fast
class InquiryEmailAttachmentTests(_PublicEndpointBase):
    """a7 — the Inquiry parent-confirmation email carries the Admissions
    Overview & Fees PDF (settings.INQUIRY_EMAIL_ATTACHMENTS), resolved via the
    same generic ADMISSIONS_ATTACHMENTS_DIR lookup as the PDF-gate download."""

    INQUIRY_URL = "/api/admissions/inquiries/"

    def _payload(self):
        return {
            "referral_source": "website",
            "guardians": [{
                "surname": "Mensah", "first_name": "Ama", "relationship": "mother",
                "religion": "Christian", "address": "12 Cantonments Rd", "town_city": "Accra",
                "phone": "+233201234567", "email": "ama@example-domain.gh",
            }],
            "students": [{
                "full_name": "Kofi Mensah", "date_of_birth": "2016-04-02",
                "current_school": "Little Steps", "current_grade": "Grade 3",
                "year_group_applied_for": "Grade 4", "academic_year": "2026/2027",
                "month_of_enrollment": "September",
            }],
        }

    def test_confirmation_email_carries_the_pdf(self):
        resp = self.client.post(self.INQUIRY_URL, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201)

        to_parent = [m for m in mail.outbox if m.to == ["ama@example-domain.gh"]]
        self.assertEqual(len(to_parent), 1)
        names = [a[0] for a in to_parent[0].attachments]
        self.assertEqual(names, ["admissions-overview-and-fees.pdf"])
        # real PDF bytes, resolved from ADMISSIONS_ATTACHMENTS_DIR
        self.assertTrue(to_parent[0].attachments[0][1].startswith(b"%PDF-"))

        # Staff alert is a separate email and is NOT bloated with the attachment.
        staff = [m for m in mail.outbox if m.to != ["ama@example-domain.gh"]]
        self.assertEqual(len(staff), 1)
        self.assertEqual(staff[0].attachments, [])

    def test_missing_attachment_file_does_not_block_the_inquiry(self):
        with tempfile.TemporaryDirectory() as d:
            with override_settings(ADMISSIONS_ATTACHMENTS_DIR=d):
                resp = self.client.post(self.INQUIRY_URL, self._payload(), format="json")
        self.assertEqual(resp.status_code, 201)
        to_parent = [m for m in mail.outbox if m.to == ["ama@example-domain.gh"]][0]
        self.assertEqual(to_parent.attachments, [])


_INQUIRY_PAYLOAD = {
    "referral_source": "website",
    "guardians": [{
        "surname": "Owusu", "first_name": "Efua", "relationship": "mother",
        "religion": "Christian", "address": "5 Ridge Rd", "town_city": "Accra",
        "phone": "+233209876543", "email": "efua@example-domain.gh",
    }],
    "students": [{
        "full_name": "Kwabena Owusu", "date_of_birth": "2015-06-10",
        "current_school": "Sunrise Prep", "current_grade": "Grade 4",
        "year_group_applied_for": "Grade 5", "academic_year": "2026/2027",
        "month_of_enrollment": "September",
    }],
}


class TransactionalEmailAsyncPathTests(_PublicEndpointBase):
    """b2 — Inquiry/Application emails go through TransactionalEmail rows +
    a Cloud Task instead of blocking the response."""

    def test_submission_persists_rows_and_enqueues_without_sending_inline(self):
        with mock.patch("admissions.emails.enqueue_transactional_ids") as enq:
            resp = self.client.post(INQUIRY_URL, _INQUIRY_PAYLOAD, format="json")

        self.assertEqual(resp.status_code, 201)
        # Nothing sent synchronously — the enqueue was mocked as a no-op.
        self.assertEqual(mail.outbox, [])
        rows = TransactionalEmail.objects.order_by("kind")
        self.assertEqual([r.kind for r in rows], ["inquiry_parent", "inquiry_staff"])
        self.assertTrue(all(r.status == "pending" for r in rows))
        self.assertEqual(rows.get(kind="inquiry_parent").to_email, "efua@example-domain.gh")
        enq.assert_called_once()
        self.assertCountEqual(list(enq.call_args[0][0]), list(rows.values_list("id", flat=True)))

    def test_worker_delivers_pending_rows(self):
        with mock.patch("admissions.emails.enqueue_transactional_ids"):
            self.client.post(INQUIRY_URL, _INQUIRY_PAYLOAD, format="json")
        ids = list(TransactionalEmail.objects.values_list("id", flat=True))

        with override_settings(BULK_EMAIL_INTERNAL_SECRET="s3cr3t"):
            resp = self.client.post(
                TRANSACTIONAL_SEND_URL, {"email_ids": ids}, format="json",
                HTTP_X_INTERNAL_SECRET="s3cr3t",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["sent"], 2)
        self.assertEqual(
            set(TransactionalEmail.objects.values_list("status", flat=True)), {"sent"},
        )
        parent = [m for m in mail.outbox if m.to == ["efua@example-domain.gh"]][0]
        self.assertEqual([a[0] for a in parent.attachments], ["admissions-overview-and-fees.pdf"])

    def test_enqueue_failure_falls_back_to_inline_send(self):
        with mock.patch(
            "admissions.emails.enqueue_transactional_ids",
            side_effect=RuntimeError("no GCP creds"),
        ):
            resp = self.client.post(INQUIRY_URL, _INQUIRY_PAYLOAD, format="json")

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(mail.outbox), 2)  # sent inline, right away
        self.assertEqual(
            set(TransactionalEmail.objects.values_list("status", flat=True)), {"sent"},
        )

    @override_settings(GCP_PROJECT_ID="")
    def test_no_queue_configured_still_delivers(self):
        # GCP_PROJECT_ID unset -> enqueue_transactional_ids raises immediately
        # (the fast-fail guard) and the inline fallback takes over. Proves
        # dev/CI need no queue at all, with no slow gRPC/credential timeout.
        resp = self.client.post(INQUIRY_URL, _INQUIRY_PAYLOAD, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(TransactionalEmail.objects.filter(status="sent").count(), 2)


@override_settings(
    BULK_EMAIL_INTERNAL_SECRET="s3cr3t",
    CLOUD_TASKS_TRANSACTIONAL_MAX_ATTEMPTS=5,
    GCP_PROJECT_ID="",  # resend_failed's enqueue fast-fails to inline
)
class TransactionalEmailWorkerTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.rows = TransactionalEmail.objects.bulk_create([
            TransactionalEmail(kind="inquiry_parent", to_email="p@example-domain.gh",
                               subject="Hi", body="body"),
            TransactionalEmail(kind="inquiry_staff", to_email="staff@example-domain.gh",
                               subject="New", body="body"),
        ])
        self.ids = [r.id for r in self.rows]

    def _post(self, secret="s3cr3t", retry="0", ids=None):
        return self.client.post(
            TRANSACTIONAL_SEND_URL, {"email_ids": self.ids if ids is None else ids},
            format="json",
            HTTP_X_INTERNAL_SECRET=secret,
            HTTP_X_CLOUDTASKS_TASKRETRYCOUNT=retry,
        )

    def test_missing_secret_is_403(self):
        resp = self.client.post(TRANSACTIONAL_SEND_URL, {"email_ids": self.ids}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(mail.outbox, [])

    def test_wrong_secret_is_403(self):
        self.assertEqual(self._post(secret="nope").status_code, 403)

    def test_unset_expected_secret_refuses_even_with_header(self):
        with override_settings(BULK_EMAIL_INTERNAL_SECRET=""):
            self.assertEqual(self._post(secret="").status_code, 403)

    def test_happy_path_marks_sent(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"processed": 2, "sent": 2})
        self.assertEqual(set(r.status for r in TransactionalEmail.objects.all()), {"sent"})
        self.assertEqual(len(mail.outbox), 2)

    def test_redelivery_is_idempotent(self):
        self._post()
        mail.outbox.clear()
        resp = self._post()  # same batch again
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["processed"], 0)
        self.assertEqual(mail.outbox, [])

    def test_transient_failure_keeps_pending_and_502s(self):
        with mock.patch("admissions.emails._deliver", side_effect=RuntimeError("smtp down")):
            resp = self._post(retry="0")
        self.assertEqual(resp.status_code, 502)
        rows = TransactionalEmail.objects.all()
        self.assertEqual(set(r.status for r in rows), {"pending"})
        self.assertEqual(set(r.attempts for r in rows), {1})
        self.assertEqual([r.last_error for r in rows], ["", ""])

    def test_final_attempt_marks_failed(self):
        with mock.patch("admissions.emails._deliver", side_effect=RuntimeError("smtp down")):
            resp = self._post(retry="4")  # MAX_ATTEMPTS - 1
        self.assertEqual(resp.status_code, 200)
        rows = TransactionalEmail.objects.all()
        self.assertEqual(set(r.status for r in rows), {"failed"})
        self.assertTrue(all("smtp down" in r.last_error for r in rows))

    def test_resend_failed_admin_action_requeues_and_sends(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from .admin import TransactionalEmailAdmin

        TransactionalEmail.objects.update(status="failed", last_error="old error")
        admin_obj = TransactionalEmailAdmin(TransactionalEmail, AdminSite())

        request = RequestFactory().post("/admin/")
        request._messages = mock.Mock()
        admin_obj.resend_failed(request, TransactionalEmail.objects.all())

        rows = TransactionalEmail.objects.all()
        self.assertEqual(set(r.status for r in rows), {"sent"})  # inline fallback (no queue)
        self.assertEqual([r.last_error for r in rows], ["", ""])
        self.assertEqual(len(mail.outbox), 2)


class AdministrationGroupTests(TestCase):
    """b5 — the "Administration" Group created by migration 0017 bundles the
    three deliberately-not-auto-granted custom admissions permissions.
    Updated 2026-09-05: also carries explicit base view/change permissions on
    every admissions model — see AdministrationCrudPermissionTests below for
    the full CRUD-grant coverage; this class stays focused on the 3 custom
    ones, which predate that fix."""

    def test_group_has_all_three_custom_permissions(self):
        from django.contrib.auth.models import Group

        group = Group.objects.get(name="Administration")
        codenames = set(group.permissions.values_list("codename", flat=True))
        self.assertTrue({"can_decide", "can_send_bulk_email", "can_view_health_info"} <= codenames)

    def test_member_of_group_has_all_three_perms(self):
        from django.contrib.auth.models import Group, User

        user = User.objects.create_user("hire", password="x")
        user.groups.add(Group.objects.get(name="Administration"))
        # re-fetch to clear the per-request permission cache
        user = User.objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("admissions.can_decide"))
        self.assertTrue(user.has_perm("admissions.can_view_health_info"))
        self.assertTrue(user.has_perm("admissions.can_send_bulk_email"))


class AdministrationCrudPermissionTests(TestCase):
    """Administration's permission set (migration 0017). 2026-09-09: widened
    from view/change-only to view+add+change on the core models (so a
    non-superuser member can actually do end-to-end admissions work — add a
    Note, create a campaign draft, set up a cycle's capacities, key in a
    walk-in family), plus view on the read-only/audit surfaces. delete_* and
    auth.* stay deliberately out — see the migration docstring."""

    # view + add + change, no delete
    CRU_MODELS = (
        "application", "student", "family", "guardian", "document", "note",
        "emergencycontact", "healthinfo", "decision", "offer", "lead",
        "emailcampaign", "capacity", "campus",
    )
    # view only
    VIEW_ONLY_MODELS = (
        "applicationdraft", "referencecounter", "transactionalemail", "emailcampaignrecipient",
    )

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("crud_admin", password="x", is_staff=True)
        cls.user.groups.add(Group.objects.get(name="Administration"))

    def _refetch(self):
        # has_perm() caches on the instance — re-fetch for a clean check.
        return User.objects.get(pk=self.user.pk)

    def test_has_view_add_change_on_every_core_model(self):
        user = self._refetch()
        for model in self.CRU_MODELS:
            with self.subTest(model=model):
                self.assertTrue(user.has_perm(f"admissions.view_{model}"))
                self.assertTrue(user.has_perm(f"admissions.add_{model}"))
                self.assertTrue(user.has_perm(f"admissions.change_{model}"))

    def test_has_view_only_on_the_readonly_surfaces(self):
        user = self._refetch()
        for model in self.VIEW_ONLY_MODELS:
            with self.subTest(model=model):
                self.assertTrue(user.has_perm(f"admissions.view_{model}"))
                self.assertFalse(user.has_perm(f"admissions.add_{model}"))
                self.assertFalse(user.has_perm(f"admissions.change_{model}"))

    def test_has_no_delete_on_anything(self):
        """The firm boundary — row cleanup stays a superuser task."""
        user = self._refetch()
        for model in self.CRU_MODELS + self.VIEW_ONLY_MODELS:
            with self.subTest(model=model):
                self.assertFalse(user.has_perm(f"admissions.delete_{model}"))

    def test_has_no_auth_administration_permissions(self):
        """Administration runs admissions; it does not administer staff
        accounts (that stays superuser-only — see deployment.md)."""
        user = self._refetch()
        for codename in ("auth.add_user", "auth.change_user", "auth.delete_user",
                         "auth.add_group", "auth.change_group", "auth.change_permission"):
            with self.subTest(codename=codename):
                self.assertFalse(user.has_perm(codename))

    def test_still_has_the_three_custom_permissions(self):
        user = self._refetch()
        self.assertTrue(user.has_perm("admissions.can_decide"))
        self.assertTrue(user.has_perm("admissions.can_view_health_info"))
        self.assertTrue(user.has_perm("admissions.can_send_bulk_email"))


class AdministrationInlineAndActionAccessTests(TestCase):
    """The functional proof, not just the raw perm bits: a genuine
    non-superuser Administration member reaches the same inline add-rows,
    audit inlines, standalone admins and actions a superuser does. Each
    assertion here corresponds to a specific gap that the 2026-09-09
    widening of migration 0017 closed."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("inline_admin", password="x", is_staff=True)
        cls.admin_user.groups.add(Group.objects.get(name="Administration"))
        cls.superuser = User.objects.create_superuser("inline_root", password="x")

    def _req(self, user):
        request = RequestFactory().get("/admin/")
        request.user = user
        return request

    def _assert_parity(self, inline_or_admin, method_name, *extra, expect):
        """method(request, *extra) matches superuser AND equals `expect` for
        the Administration member. `extra` carries the parent obj that an
        inline's has_add_permission requires as a positional arg (ModelAdmin's
        takes only request; has_view_permission defaults obj to None)."""
        method = getattr(inline_or_admin, method_name)
        admin_result = method(self._req(self.admin_user), *extra)
        su_result = method(self._req(self.superuser), *extra)
        self.assertEqual(admin_result, su_result, f"{inline_or_admin}.{method_name} parity")
        self.assertEqual(admin_result, expect, f"{inline_or_admin}.{method_name} value")

    def test_inline_add_rows_are_available(self):
        from .admin import (
            DocumentInline, EmergencyContactInline, GuardianInline, NoteInline, StudentInline,
        )
        self._assert_parity(NoteInline(Application, admin.site), "has_add_permission", None, expect=True)
        self._assert_parity(GuardianInline(Family, admin.site), "has_add_permission", None, expect=True)
        self._assert_parity(StudentInline(Family, admin.site), "has_add_permission", None, expect=True)
        self._assert_parity(DocumentInline(Application, admin.site), "has_add_permission", None, expect=True)
        self._assert_parity(
            EmergencyContactInline(Application, admin.site), "has_add_permission", None, expect=True
        )

    def test_bulk_send_audit_inline_is_visible(self):
        from .admin import EmailCampaignRecipientInline
        self._assert_parity(
            EmailCampaignRecipientInline(EmailCampaign, admin.site), "has_view_permission", expect=True
        )

    def test_can_create_campaign_drafts_and_capacities(self):
        from .admin import CapacityAdmin, EmailCampaignAdmin
        from .models import Capacity
        self._assert_parity(EmailCampaignAdmin(EmailCampaign, admin.site), "has_add_permission", expect=True)
        self._assert_parity(CapacityAdmin(Capacity, admin.site), "has_add_permission", expect=True)

    def test_readonly_support_admins_are_viewable_not_editable(self):
        from .admin import ApplicationDraftAdmin, ReferenceCounterAdmin, TransactionalEmailAdmin
        from .models import ApplicationDraft, ReferenceCounter

        self._assert_parity(
            TransactionalEmailAdmin(TransactionalEmail, admin.site), "has_view_permission", expect=True
        )
        self._assert_parity(
            ApplicationDraftAdmin(ApplicationDraft, admin.site), "has_view_permission", expect=True
        )
        rc_admin = ReferenceCounterAdmin(ReferenceCounter, admin.site)
        self._assert_parity(rc_admin, "has_view_permission", expect=True)
        # ReferenceCounter is view-only for Administration on purpose
        # ("never hand-edited in normal operation").
        self.assertFalse(rc_admin.has_change_permission(self._req(self.admin_user)))

    def test_transactional_email_resend_action_is_available(self):
        from .admin import TransactionalEmailAdmin
        actions = TransactionalEmailAdmin(TransactionalEmail, admin.site).get_actions(self._req(self.admin_user))
        self.assertIn("resend_failed", actions)


def _make_application(year_group, stage="inquiry", campus=None, academic_year="2026/2027"):
    family = Family.objects.create()
    student = Student.objects.create(family=family, full_name=f"Student ({year_group})")
    return Application.objects.create(
        student=student, stage=stage, academic_year=academic_year,
        year_group_applied_for=year_group, campus=campus,
    )


def _make_coordinator(username, band):
    user = User.objects.create_user(username, password="x", is_staff=True)
    StaffProfile.objects.create(user=user, grade_band=band)
    user.groups.add(Group.objects.get(name=access.COORDINATOR_GROUP_BY_BAND[band]))
    return user


def _make_administration_user(username="office_admin"):
    user = User.objects.create_user(username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name="Administration"))
    return user


def _admin_request(user):
    request = RequestFactory().get("/admin/admissions/application/")
    request.user = user
    return request


class GradeBandDefinitionTests(TestCase):
    """Sanity-checks the single source of truth GRADE_BANDS is derived
    from (STUDENT_ID_CLASSIFICATION) rather than a second, hand-maintained
    grade list that could silently drift from it."""

    def test_bands_are_disjoint(self):
        from .models import GRADE_BANDS
        preschool, primary, jhs = GRADE_BANDS["preschool"], GRADE_BANDS["primary"], GRADE_BANDS["jhs"]
        self.assertEqual(preschool & primary, frozenset())
        self.assertEqual(primary & jhs, frozenset())
        self.assertEqual(preschool & jhs, frozenset())

    def test_bands_cover_exactly_the_classified_grades(self):
        from .models import GRADE_BANDS, STUDENT_ID_CLASSIFICATION
        union = GRADE_BANDS["preschool"] | GRADE_BANDS["primary"] | GRADE_BANDS["jhs"]
        self.assertEqual(union, set(STUDENT_ID_CLASSIFICATION))

    def test_grade_10_has_no_band(self):
        # TCS doesn't offer SHS yet — no classification code, so no band either.
        from .models import GRADE_BANDS
        self.assertNotIn("Grade 10", GRADE_BANDS["preschool"] | GRADE_BANDS["primary"] | GRADE_BANDS["jhs"])


class GradeBandCoordinatorScopingTests(TestCase):
    """Phase 6.2 — the actual queryset filtering, exercised the way the real
    admin uses it: GradeBandScopedAdmin.get_queryset() via RequestFactory,
    not just scoped_grades_for() in isolation."""

    @classmethod
    def setUpTestData(cls):
        cls.main = Campus.objects.get(name="Main")
        cls.annex = Campus.objects.get(name="Annex")

        # Preschool band, deliberately split across both campuses (Annex only
        # accepts Pre Nursery/Nursery 1 — both are Preschool-band grades).
        cls.app_preschool_main = _make_application("Pre Nursery", campus=cls.main)
        cls.app_preschool_annex = _make_application("Nursery 1", campus=cls.annex)
        cls.app_primary = _make_application("Grade 3", campus=cls.main)
        cls.app_jhs = _make_application("Grade 8", campus=cls.main)
        cls.app_unbanded = _make_application("Grade 11", campus=cls.main)  # no classification code

        cls.preschool_user = _make_coordinator("preschool_coord", "preschool")
        cls.primary_user = _make_coordinator("primary_coord", "primary")
        cls.jhs_user = _make_coordinator("jhs_coord", "jhs")
        cls.admin_user = _make_administration_user()
        cls.superuser = User.objects.create_superuser("root", password="x")

        # Misconfigured: in a Coordinator group, but no StaffProfile at all.
        cls.misconfigured_user = User.objects.create_user("no_profile_coord", password="x", is_staff=True)
        cls.misconfigured_user.groups.add(Group.objects.get(name="Primary Coordinator"))

    def _visible_ids(self, user):
        admin_obj = ApplicationAdmin(Application, admin.site)
        return set(admin_obj.get_queryset(_admin_request(user)).values_list("id", flat=True))

    def test_preschool_coordinator_sees_own_band_across_both_campuses(self):
        visible = self._visible_ids(self.preschool_user)
        self.assertEqual(visible, {self.app_preschool_main.id, self.app_preschool_annex.id})

    def test_primary_coordinator_sees_only_primary_band(self):
        self.assertEqual(self._visible_ids(self.primary_user), {self.app_primary.id})

    def test_jhs_coordinator_sees_only_jhs_band(self):
        self.assertEqual(self._visible_ids(self.jhs_user), {self.app_jhs.id})

    def test_unbanded_grade_visible_only_to_administration_and_superuser(self):
        for user in (self.preschool_user, self.primary_user, self.jhs_user):
            self.assertNotIn(self.app_unbanded.id, self._visible_ids(user))
        self.assertIn(self.app_unbanded.id, self._visible_ids(self.admin_user))
        self.assertIn(self.app_unbanded.id, self._visible_ids(self.superuser))

    def test_administration_and_superuser_see_everything(self):
        all_ids = {
            self.app_preschool_main.id, self.app_preschool_annex.id,
            self.app_primary.id, self.app_jhs.id, self.app_unbanded.id,
        }
        self.assertEqual(self._visible_ids(self.admin_user), all_ids)
        self.assertEqual(self._visible_ids(self.superuser), all_ids)

    def test_misconfigured_coordinator_sees_nothing(self):
        self.assertEqual(self._visible_ids(self.misconfigured_user), set())

    def test_grade_change_moves_application_between_coordinators_live(self):
        """The b6.2 headline requirement: a live queryset filter, not a
        stored assignment, so a post-submission grade edit re-resolves
        without any backfill."""
        app = _make_application("Grade 6", campus=self.main)  # primary band
        self.assertIn(app.id, self._visible_ids(self.primary_user))
        self.assertNotIn(app.id, self._visible_ids(self.jhs_user))

        app.year_group_applied_for = "Grade 7"  # now JHS band
        app.save()

        self.assertNotIn(app.id, self._visible_ids(self.primary_user))
        self.assertIn(app.id, self._visible_ids(self.jhs_user))

    def test_has_change_permission_blocks_out_of_band_object(self):
        admin_obj = ApplicationAdmin(Application, admin.site)
        request = _admin_request(self.primary_user)
        self.assertFalse(admin_obj.has_change_permission(request, self.app_jhs))
        self.assertTrue(admin_obj.has_change_permission(request, self.app_primary))

        # Superuser: scoped_grades_for() returns None *and* Django's own
        # has_change_permission is unconditionally True for a superuser.
        su_request = _admin_request(self.superuser)
        self.assertTrue(admin_obj.has_change_permission(su_request, self.app_jhs))

    def test_administration_bypasses_the_band_scoping_check_itself(self):
        """Isolates GradeBandScopedAdmin's OWN bypass (scoped_grades_for()
        returning None for an Administration member) from Django's base
        model permissions, which are a separate mechanism entirely (see
        test_administration_member_passes_same_checks_as_superuser below,
        which exercises the two together — this test stays useful on its
        own as documentation of what GradeBandScopedAdmin itself is
        responsible for)."""
        admin_obj = ApplicationAdmin(Application, admin.site)
        request = _admin_request(self.admin_user)
        self.assertTrue(admin_obj._in_scope(request, self.app_jhs))

    def test_administration_member_passes_same_checks_as_superuser(self):
        """2026-09-05 fix — the "Administration" group now grants explicit
        base view/change permissions (migration 0017), not just its 3 custom
        ones, so a genuinely NON-superuser Administration member now passes
        exactly the same admin checks a superuser does — the gap flagged
        after the first Phase 6.2 pass is closed. self.admin_user here is
        deliberately not a superuser (see setUpTestData)."""
        self.assertFalse(self.admin_user.is_superuser)

        checks = [
            (ApplicationAdmin(Application, admin.site), self.app_jhs),
            (StudentAdmin(Student, admin.site), self.app_jhs.student),
            (FamilyAdmin(Family, admin.site), self.app_jhs.student.family),
            (GuardianAdmin(Guardian, admin.site), None),
        ]
        for admin_obj, obj in checks:
            admin_request = _admin_request(self.admin_user)
            su_request = _admin_request(self.superuser)
            self.assertEqual(
                admin_obj.has_view_permission(admin_request, obj),
                admin_obj.has_view_permission(su_request, obj),
            )
            self.assertEqual(
                admin_obj.has_change_permission(admin_request, obj),
                admin_obj.has_change_permission(su_request, obj),
            )
            self.assertTrue(admin_obj.has_view_permission(admin_request, obj))
            self.assertTrue(admin_obj.has_change_permission(admin_request, obj))

    def test_family_admin_filtered_and_distinct_with_multiple_in_band_children(self):
        family = Family.objects.create()
        s1 = Student.objects.create(family=family, full_name="Kid One")
        s2 = Student.objects.create(family=family, full_name="Kid Two")
        Application.objects.create(student=s1, stage="inquiry", academic_year="2026/2027",
                                    year_group_applied_for="Pre Nursery")
        Application.objects.create(student=s2, stage="inquiry", academic_year="2026/2027",
                                    year_group_applied_for="Nursery 2")

        admin_obj = FamilyAdmin(Family, admin.site)
        qs = admin_obj.get_queryset(_admin_request(self.preschool_user))
        # .distinct() must collapse the two-child join fan-out to one row.
        self.assertEqual(qs.filter(pk=family.pk).count(), 1)

    def test_readonly_fields_for_coordinator_are_the_whole_model(self):
        admin_obj = ApplicationAdmin(Application, admin.site)
        coordinator_fields = set(admin_obj.get_readonly_fields(_admin_request(self.primary_user), self.app_primary))
        self.assertIn("stage", coordinator_fields)
        self.assertIn("campus", coordinator_fields)
        self.assertIn("year_group_applied_for", coordinator_fields)

        admin_fields = set(admin_obj.get_readonly_fields(_admin_request(self.admin_user), self.app_primary))
        self.assertNotIn("stage", admin_fields)  # Administration keeps the normal small readonly set

    def test_get_actions_hides_unrestricted_stage_actions_for_coordinator(self):
        admin_obj = ApplicationAdmin(Application, admin.site)
        coordinator_actions = admin_obj.get_actions(_admin_request(self.primary_user))
        self.assertNotIn("mark_as_application", coordinator_actions)
        self.assertNotIn("mark_as_document_review", coordinator_actions)
        self.assertNotIn("mark_as_enrolled", coordinator_actions)
        self.assertIn("move_to_document_review", coordinator_actions)

        admin_actions = admin_obj.get_actions(_admin_request(self.admin_user))
        self.assertIn("mark_as_document_review", admin_actions)
        self.assertIn("move_to_document_review", admin_actions)

    def test_health_info_inline_scoped_to_own_band(self):
        from .models import HealthInfo
        HealthInfo.objects.create(application=self.app_preschool_main)
        HealthInfo.objects.create(application=self.app_jhs)

        inline = HealthInfoInline(Application, admin.site)
        visible = inline.get_queryset(_admin_request(self.preschool_user))
        self.assertEqual(list(visible.values_list("application_id", flat=True)), [self.app_preschool_main.id])

    def test_document_inline_scoped_to_own_band(self):
        from .models import Document
        Document.objects.create(application=self.app_primary, document_type="other")
        Document.objects.create(application=self.app_jhs, document_type="other")

        inline = DocumentInline(Application, admin.site)
        visible = inline.get_queryset(_admin_request(self.primary_user))
        self.assertEqual(list(visible.values_list("application_id", flat=True)), [self.app_primary.id])


class MoveToDocumentReviewTests(TestCase):
    """The one write a coordinator can trigger — see
    Application.move_to_document_review() in models.py."""

    def test_moves_from_inquiry(self):
        app = _make_application("Grade 2", stage="inquiry")
        result = app.move_to_document_review()
        self.assertEqual(result.stage, "document_review")
        app.refresh_from_db()
        self.assertEqual(app.stage, "document_review")

    def test_moves_from_application_and_assigns_reference_via_real_save(self):
        # Created directly at "inquiry" with no application_reference, same as
        # a real public Inquiry submission — proves this goes through the
        # real save() pipeline (which assigns application_reference for
        # document_review, per STAGES_REQUIRING_APPLICATION_REFERENCE), not a
        # raw field write that would skip it.
        app = _make_application("Grade 2", stage="inquiry")
        self.assertIsNone(app.application_reference)
        app.move_to_document_review()
        app.refresh_from_db()
        self.assertIsNotNone(app.application_reference)
        self.assertTrue(app.application_reference.startswith("APP-"))

    def test_rejects_when_already_past_application_stage(self):
        app = _make_application("Grade 2", stage="document_review")
        with self.assertRaises(ValidationError):
            app.move_to_document_review()
        app.refresh_from_db()
        self.assertEqual(app.stage, "document_review")  # unchanged

    def test_rejects_from_terminal_stages(self):
        for stage in ("rejected", "waitlisted", "offer_declined", "offer", "enrolled"):
            app = _make_application("Grade 2", stage="inquiry")
            # Application.save()'s own gate blocks *entering* offer/enrolled
            # without an accepted Decision/Offer — irrelevant to what's under
            # test here (move_to_document_review's OWN precondition), so
            # write the starting stage directly rather than fighting that
            # unrelated gate to construct the fixture.
            Application.objects.filter(pk=app.pk).update(stage=stage)
            app.refresh_from_db()

            with self.assertRaises(ValidationError):
                app.move_to_document_review()
            app.refresh_from_db()
            self.assertEqual(app.stage, stage)  # never silently changed

    def test_double_call_is_safe_not_a_double_transition(self):
        app = _make_application("Grade 2", stage="inquiry")
        app.move_to_document_review()
        with self.assertRaises(ValidationError):
            app.move_to_document_review()  # second call — already document_review now
        app.refresh_from_db()
        self.assertEqual(app.stage, "document_review")


class MoveToDocumentReviewAdminActionTests(TestCase):
    def test_partial_success_reports_succeeded_and_skipped(self):
        eligible = _make_application("Grade 3", stage="inquiry")
        ineligible = _make_application("Grade 3", stage="rejected")

        admin_obj = ApplicationAdmin(Application, admin.site)
        request = _admin_request(_make_administration_user())
        request._messages = mock.Mock()

        admin_obj.move_to_document_review(
            request, Application.objects.filter(pk__in=[eligible.pk, ineligible.pk])
        )

        eligible.refresh_from_db()
        ineligible.refresh_from_db()
        self.assertEqual(eligible.stage, "document_review")
        self.assertEqual(ineligible.stage, "rejected")  # untouched


class AuditStaffRolesCommandTests(TestCase):
    def _run(self):
        out = io.StringIO()
        call_command("audit_staff_roles", stdout=out)
        return out.getvalue()

    def test_clean_setup_reports_no_problems(self):
        _make_coordinator("clean_coord", "preschool")
        _make_administration_user()
        self.assertIn("No staff-role inconsistencies found.", self._run())

    def test_flags_coordinator_group_without_band(self):
        user = User.objects.create_user("bandless", password="x", is_staff=True)
        user.groups.add(Group.objects.get(name="JHS Coordinator"))
        output = self._run()
        self.assertIn("bandless", output)
        self.assertIn("fails closed", output)

    def test_flags_band_without_matching_group(self):
        user = User.objects.create_user("groupless", password="x", is_staff=True)
        StaffProfile.objects.create(user=user, grade_band="jhs")
        output = self._run()
        self.assertIn("groupless", output)
        self.assertIn("has scope with none of the coordinator permissions", output)

    def test_flags_administration_and_coordinator_together(self):
        user = _make_coordinator("dual_role", "primary")
        user.groups.add(Group.objects.get(name="Administration"))
        output = self._run()
        self.assertIn("dual_role", output)
        self.assertIn("Administration wins", output)
