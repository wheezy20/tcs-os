"""End-to-end coverage for the Lead capture feature: the two public
endpoints (quick-interest widget, PDF gate), Turnstile gating, the
consent-default safety net, bulk-email audience targeting, unsubscribe for
both Guardians and Leads, and the {{recipient_*}} placeholder aliases.

Turnstile is patched out (it makes a real Cloudflare HTTP call); email uses
Django's in-memory backend, so mail.outbox is asserted directly.
"""

import os
import tempfile
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient

from . import bulk_email, emails
from .models import (
    Application, Campus, EmailCampaign, EmailCampaignRecipient, Family, Guardian, Lead, Student,
    TransactionalEmail,
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
