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

from . import bulk_email
from .models import (
    Application, Campus, EmailCampaign, EmailCampaignRecipient, Family, Guardian, Lead, Student,
)

QUICK_INTEREST_URL = "/api/admissions/quick-interest/"
PDF_GATE_URL = "/api/admissions/pdf-gate/admissions-overview/"

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
