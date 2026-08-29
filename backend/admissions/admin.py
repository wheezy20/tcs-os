import logging
from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, StackedInline, TabularInline

from . import bulk_email, emails, storage
from .models import (
    Application, ApplicationDraft, Campus, Capacity, Decision, Document, EmailCampaign,
    EmailCampaignRecipient, EmergencyContact, Family, Guardian, HealthInfo, Note, Offer,
    ReferenceCounter, Student,
)

logger = logging.getLogger(__name__)


class GuardianInline(TabularInline):
    model = Guardian
    extra = 0


class StudentInline(TabularInline):
    model = Student
    extra = 0


@admin.register(Family)
class FamilyAdmin(ModelAdmin):
    inlines = [GuardianInline, StudentInline]
    list_display = ("__str__", "referral_source", "created_at")
    list_filter = ("referral_source",)


class DocumentInline(TabularInline):
    model = Document
    extra = 0
    fields = ("document_type", "file_path", "file_link", "status", "uploaded_at")
    readonly_fields = ("file_link",)

    @admin.display(description="")
    def file_link(self, obj):
        """Mints a fresh signed URL per page load rather than storing one —
        the bucket is private, so a stored URL would eventually expire
        silently. See Document.file_path's help text."""
        if not obj.file_path:
            return "—"
        try:
            url = storage.create_read_url(obj.file_path)
        except storage.SupabaseStorageError:
            return "(could not sign URL)"
        return format_html('<a href="{}" target="_blank" rel="noopener">Open</a>', url)


class NoteInline(TabularInline):
    model = Note
    extra = 0
    readonly_fields = ("author", "created_at")


class DecisionInline(StackedInline):
    model = Decision
    extra = 1  # exactly one blank form available when no Decision exists yet
    max_num = 1
    readonly_fields = ("decided_by", "decided_at")

    def _can_decide(self, request):
        return request.user.has_perm("admissions.can_decide")

    def has_add_permission(self, request, obj=None):
        return self._can_decide(request)

    def has_change_permission(self, request, obj=None):
        return self._can_decide(request)

    def has_delete_permission(self, request, obj=None):
        return self._can_decide(request)


class OfferInline(StackedInline):
    """Offers are created via the "Generate Offer" action (it needs to mint a
    token and send an email — real side effects, not just a form save), not
    added directly here. The inline exists so staff can see it and record a
    phone-confirmed response (`response` is the one editable field)."""
    model = Offer
    extra = 0
    max_num = 1
    fields = ("token", "sent_at", "expires_at", "response", "responded_at")
    readonly_fields = ("token", "sent_at", "expires_at", "responded_at")

    def _can_decide(self, request):
        return request.user.has_perm("admissions.can_decide")

    def has_add_permission(self, request, obj=None):
        return False  # only ever created via the generate_offer action

    def has_change_permission(self, request, obj=None):
        return self._can_decide(request)

    def has_delete_permission(self, request, obj=None):
        return self._can_decide(request)


class EmergencyContactInline(TabularInline):
    model = EmergencyContact
    extra = 0


class HealthInfoInline(StackedInline):
    """Real health data about a child — gated behind admissions.can_view_health_info
    so staff without that permission don't see this section exists at all,
    not just a read-only view of it. See HealthInfo's own docstring."""
    model = HealthInfo
    extra = 0
    max_num = 1

    def _can_view_health_info(self, request):
        return request.user.has_perm("admissions.can_view_health_info")

    def has_view_permission(self, request, obj=None):
        return self._can_view_health_info(request)

    def has_add_permission(self, request, obj=None):
        return self._can_view_health_info(request)

    def has_change_permission(self, request, obj=None):
        return self._can_view_health_info(request)

    def has_delete_permission(self, request, obj=None):
        return self._can_view_health_info(request)


@admin.register(Application)
class ApplicationAdmin(ModelAdmin):
    inlines = [DecisionInline, OfferInline, DocumentInline, EmergencyContactInline, HealthInfoInline, NoteInline]
    list_display = (
        "student", "campus", "year_group_applied_for", "academic_year", "month_of_enrollment",
        "stage", "inquiry_reference", "application_reference", "updated_at",
    )
    list_filter = ("stage", "campus", "academic_year", "year_group_applied_for")
    search_fields = ("student__full_name", "inquiry_reference", "application_reference")
    readonly_fields = ("inquiry_reference", "application_reference", "declaration_agreed_at", "declaration_ip_address")
    actions = [
        "mark_as_application", "mark_as_document_review",
        "generate_offer", "reset_offer", "mark_as_enrolled",
    ]

    def get_actions(self, request):
        """Hides (and, per Django admin's own dispatch, functionally blocks —
        response_action() looks the submitted action up in this same dict)
        the can_decide-gated actions for anyone without that permission."""
        actions = super().get_actions(request)
        if not request.user.has_perm("admissions.can_decide"):
            actions.pop("generate_offer", None)
            actions.pop("reset_offer", None)
        return actions

    def save_formset(self, request, form, formset, change):
        """NoteInline marks 'author' readonly, DecisionInline marks
        'decided_by' readonly — neither was being set anywhere else, so both
        would otherwise save as NULL. Assign the logged-in staff user here."""
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if isinstance(instance, Note) and not instance.pk:
                instance.author = request.user
            if isinstance(instance, Decision):
                instance.decided_by = request.user
            instance.save()
            if isinstance(instance, Decision):
                self._warn_if_over_capacity(request, instance)
        formset.save_m2m()

    def _warn_if_over_capacity(self, request, decision):
        """Soft warning only — never blocks the save. Real admissions has
        legitimate reasons to go over capacity on paper (sibling priority,
        board exceptions), so this is informational, not a gate. Scoped by
        campus too (Phase 5) — TCS's two campuses are physically separate
        seat pools, so a campus=None Application only ever matches a
        campus=None Capacity row, never conflating the two."""
        if decision.decision_type != "accepted":
            return

        application = decision.application
        capacity = Capacity.objects.filter(
            academic_year=application.academic_year,
            year_group=application.year_group_applied_for,
            campus=application.campus,
        ).first()
        if not capacity:
            return  # no capacity defined for this (year, grade, campus) — nothing to compare against

        accepted_count = Decision.objects.filter(
            decision_type="accepted",
            application__academic_year=application.academic_year,
            application__year_group_applied_for=application.year_group_applied_for,
            application__campus=application.campus,
        ).count()

        if accepted_count > capacity.capacity:
            campus_label = f" @ {application.campus}" if application.campus else ""
            self.message_user(
                request,
                f"Capacity warning: {application.year_group_applied_for} "
                f"({application.academic_year}{campus_label}) now has {accepted_count} accepted "
                f"decision(s) against a capacity of {capacity.capacity}.",
                level=messages.WARNING,
            )

    def _bulk_set_stage(self, request, queryset, stage, label):
        """queryset.update() would be a raw bulk SQL UPDATE that bypasses
        Application.save() entirely — and both reference-number/student_id
        assignment AND the Phase 3 stage gate live in that save() override.
        Must save() each instance individually so those actually fire, and
        must catch ValidationError per-row so one gate failure doesn't kill
        the whole batch or look like a crash to staff."""
        succeeded = 0
        failed = []
        for application in queryset:
            application.stage = stage
            try:
                application.save()
                succeeded += 1
            except ValidationError as exc:
                failed.append((application, exc))

        self.message_user(request, f"{succeeded} application(s) moved to {label} stage.")
        if failed:
            detail = "; ".join(f"{app}: {exc.message}" for app, exc in failed[:5])
            if len(failed) > 5:
                detail += f"; (+{len(failed) - 5} more)"
            self.message_user(
                request,
                f"{len(failed)} application(s) could NOT be moved to {label}: {detail}",
                level=messages.WARNING,
            )

    @admin.action(description="Move selected to: Application")
    def mark_as_application(self, request, queryset):
        self._bulk_set_stage(request, queryset, "application", "Application")

    @admin.action(description="Move selected to: Document Review")
    def mark_as_document_review(self, request, queryset):
        self._bulk_set_stage(request, queryset, "document_review", "Document Review")

    @admin.action(description="Move selected to: Enrolled")
    def mark_as_enrolled(self, request, queryset):
        """The old 'only from Offer stage' pre-filter (Phase 3's own
        predecessor stopgap) is gone — the model-level gate in
        Application.save() now enforces the real requirement (an accepted
        Decision AND an accepted Offer), and _bulk_set_stage() reports
        per-row failures clearly. One source of truth instead of two."""
        self._bulk_set_stage(request, queryset, "enrolled", "Enrolled")

    @admin.action(description="Generate Offer (requires accepted Decision, admissions.can_decide)")
    def generate_offer(self, request, queryset):
        if not request.user.has_perm("admissions.can_decide"):
            self.message_user(request, "You don't have permission to generate offers.", level=messages.ERROR)
            return

        succeeded = 0
        failed = []
        for application in queryset:
            offer, _created = Offer.objects.get_or_create(application=application)
            offer.response = "pending"
            offer.sent_at = timezone.now()
            offer.expires_at = timezone.now() + timedelta(days=settings.OFFER_EXPIRY_DAYS)
            offer.responded_at = None

            application.stage = "offer"
            try:
                offer.save()
                application.save()
                emails.send_offer_email(offer)
                succeeded += 1
            except ValidationError as exc:
                failed.append((application, exc))

        self.message_user(request, f"{succeeded} offer(s) generated and emailed.")
        if failed:
            detail = "; ".join(f"{app}: {exc.message}" for app, exc in failed[:5])
            self.message_user(
                request, f"{len(failed)} application(s) could NOT get an offer: {detail}",
                level=messages.WARNING,
            )

    @admin.action(description="Reset Offer (back to Pending, admissions.can_decide)")
    def reset_offer(self, request, queryset):
        if not request.user.has_perm("admissions.can_decide"):
            self.message_user(request, "You don't have permission to reset offers.", level=messages.ERROR)
            return

        count = 0
        for application in queryset:
            offer = getattr(application, "offer", None)
            if not offer:
                continue
            offer.response = "pending"
            offer.sent_at = timezone.now()
            offer.expires_at = timezone.now() + timedelta(days=settings.OFFER_EXPIRY_DAYS)
            offer.responded_at = None
            offer.save()
            count += 1
        self.message_user(request, f"{count} offer(s) reset to Pending with a fresh expiry.")


@admin.register(Student)
class StudentAdmin(ModelAdmin):
    list_display = (
        "full_name", "family", "date_of_birth", "gender", "nationality",
        "current_grade", "current_school", "student_id",
    )
    search_fields = ("full_name", "student_id")
    readonly_fields = ("student_id",)


class BulkEmailSubscribedFilter(admin.SimpleListFilter):
    title = "bulk email subscription"
    parameter_name = "bulk_subscribed"

    def lookups(self, request, model_admin):
        return [("yes", "Subscribed"), ("no", "Unsubscribed")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(bulk_email_unsubscribed_at__isnull=True)
        if self.value() == "no":
            return queryset.filter(bulk_email_unsubscribed_at__isnull=False)
        return queryset


@admin.register(Guardian)
class GuardianAdmin(ModelAdmin):
    list_display = ("full_name", "family", "relationship", "email", "phone", "town_city", "bulk_email_unsubscribed_at")
    list_filter = (BulkEmailSubscribedFilter,)
    search_fields = ("first_name", "surname", "email")
    readonly_fields = ("bulk_email_unsubscribe_token",)


@admin.register(ReferenceCounter)
class ReferenceCounterAdmin(ModelAdmin):
    """Visibility/debugging only — these rows are maintained entirely by
    ReferenceCounter.next_for(), never hand-edited in normal operation."""
    list_display = ("key", "next_value")
    search_fields = ("key",)


@admin.register(Capacity)
class CapacityAdmin(ModelAdmin):
    list_display = ("year_group", "academic_year", "campus", "capacity")
    list_filter = ("academic_year", "campus")
    search_fields = ("year_group",)


@admin.register(Campus)
class CampusAdmin(ModelAdmin):
    list_display = ("name",)


@admin.register(ApplicationDraft)
class ApplicationDraftAdmin(ModelAdmin):
    """Visibility/support only — staff can look up a stuck parent's draft by
    email, but the JSON blob isn't meant to be hand-edited. Note this can
    contain the same health/wellbeing data HealthInfoInline restricts
    elsewhere, since it's whatever step of the multi-step form the parent
    last saved."""
    list_display = ("email", "current_step", "is_submitted", "created_at", "expires_at")
    list_filter = ("current_step",)
    search_fields = ("email", "token")
    readonly_fields = ("token", "created_at", "updated_at", "submitted_application")

    @admin.display(boolean=True)
    def is_submitted(self, obj):
        return obj.is_submitted


class EmailCampaignRecipientInline(TabularInline):
    """The audit trail — who a campaign was actually sent to, and whether it
    sent. Read-only: rows are only ever created by the send_campaign action
    and only ever updated by BulkEmailBatchSendView, never hand-edited."""
    model = EmailCampaignRecipient
    extra = 0
    fields = ("email", "status", "sent_at", "resend_message_id", "error_message")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailCampaign)
class EmailCampaignAdmin(ModelAdmin):
    """Phase 6 — bulk/marketing email. Drafting and Preview need only normal
    admin access; actually triggering a real send to hundreds/thousands of
    families is gated behind admissions.can_send_bulk_email (not
    auto-granted — same deliberate-grant treatment HealthInfo's permission
    got), given the blast radius of sending the wrong campaign to everyone
    by mistake."""
    list_display = (
        "name", "status", "total_recipients", "sent_count", "failed_count", "skipped_count", "created_at", "sent_at",
    )
    list_filter = ("status",)
    search_fields = ("name", "subject")
    readonly_fields = (
        "status", "created_by", "sent_at", "total_recipients", "sent_count", "failed_count", "skipped_count",
    )
    inlines = [EmailCampaignRecipientInline]
    actions = ["preview_campaign", "send_campaign", "retry_failed_recipients"]

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.has_perm("admissions.can_send_bulk_email"):
            actions.pop("send_campaign", None)
            actions.pop("retry_failed_recipients", None)
        return actions

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Preview (renders against one real matching recipient)")
    def preview_campaign(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one campaign to preview.", level=messages.ERROR)
            return None

        campaign = queryset.first()
        recipients = bulk_email.compute_recipients(campaign)
        sample_guardian = recipients.first()
        if sample_guardian:
            context = bulk_email.build_placeholder_context(sample_guardian)
            sample_note = f"Rendered against a real matching recipient: {sample_guardian.full_name} <{sample_guardian.email}>"
        else:
            # No matching recipient yet (e.g. a narrow filter combination) —
            # preview should still work, just with placeholder text instead
            # of real data, so staff can proofread the template itself.
            context = {
                "guardian_first_name": "(guardian first name)",
                "guardian_full_name": "(guardian full name)",
                "student_names": "(student name(s))",
                "unsubscribe_link": "(unsubscribe link — generated per recipient at send time)",
            }
            sample_note = "No recipient currently matches this campaign's filters — showing placeholder text instead of real data."

        rendered_subject = bulk_email.render_template(campaign.subject, context)
        rendered_body = bulk_email.render_template(campaign.body, context)

        return TemplateResponse(
            request,
            "admin/admissions/emailcampaign/preview.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Preview — {campaign.name}",
                "campaign": campaign,
                "recipient_count": recipients.count(),
                "sample_note": sample_note,
                "rendered_subject": rendered_subject,
                "rendered_body": rendered_body,
                "opts": self.model._meta,
            },
        )

    @admin.action(description="Send (requires admissions.can_send_bulk_email)")
    def send_campaign(self, request, queryset):
        if not request.user.has_perm("admissions.can_send_bulk_email"):
            self.message_user(request, "You don't have permission to send bulk email.", level=messages.ERROR)
            return

        for campaign in queryset:
            if campaign.status != "draft":
                self.message_user(
                    request, f'"{campaign.name}" is already {campaign.get_status_display()} — skipped.',
                    level=messages.WARNING,
                )
                continue

            try:
                campaign.full_clean()
            except ValidationError as exc:
                self.message_user(request, f'"{campaign.name}": {exc}', level=messages.ERROR)
                continue

            recipients = bulk_email.compute_recipients(campaign)
            recipient_count = recipients.count()
            if recipient_count == 0:
                self.message_user(
                    request, f'"{campaign.name}" has no matching recipients (all filtered out or unsubscribed) — not queued.',
                    level=messages.WARNING,
                )
                continue

            with transaction.atomic():
                EmailCampaignRecipient.objects.bulk_create([
                    EmailCampaignRecipient(campaign=campaign, guardian=guardian, email=guardian.email)
                    for guardian in recipients
                ])
                campaign.total_recipients = recipient_count
                campaign.status = "queued"
                campaign.save(update_fields=["total_recipients", "status"])

            # Enqueueing itself happens *outside* the DB transaction (Cloud
            # Tasks is a real network call, no point holding a DB lock for
            # it) — if it fails (e.g. a transient GCP issue), roll the
            # recipient rows and status back to draft rather than leaving
            # "queued" with nothing actually dispatched and no way to retry
            # except by hand.
            try:
                task_count, skipped_count = bulk_email.enqueue_campaign_send(campaign)
            except Exception:
                logger.exception("Failed to enqueue Cloud Tasks for campaign %s", campaign.id)
                EmailCampaignRecipient.objects.filter(campaign=campaign).delete()
                campaign.total_recipients = 0
                campaign.status = "draft"
                campaign.save(update_fields=["total_recipients", "status"])
                self.message_user(
                    request,
                    f'"{campaign.name}": could not queue the send (a background-job error) — reverted to Draft, nothing was sent. Try again once the issue is resolved.',
                    level=messages.ERROR,
                )
                continue

            queued_count = recipient_count - skipped_count
            if queued_count == 0:
                # Every recipient had an invalid/placeholder address — enqueue_campaign_send
                # already finalized the campaign itself (there's no Cloud Task to do it later).
                self.message_user(
                    request,
                    f'"{campaign.name}": all {skipped_count} recipient(s) had an invalid/placeholder '
                    f'address — none were sent to Resend. Campaign marked {campaign.get_status_display()}.',
                    level=messages.ERROR,
                )
            elif skipped_count:
                self.message_user(
                    request,
                    f'"{campaign.name}" queued: {queued_count} recipient(s) across {task_count} batch(es) '
                    f'— {skipped_count} skipped (invalid/placeholder address, never sent to Resend; see the recipient audit).',
                    level=messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    f'"{campaign.name}" queued: {queued_count} recipient(s) across {task_count} batch(es).',
                )

    @admin.action(description="Retry failed recipients (requires admissions.can_send_bulk_email)")
    def retry_failed_recipients(self, request, queryset):
        """Deliberately narrower than send_campaign: only re-dispatches rows
        currently status="failed" or "skipped_invalid" (a Resend-side
        rejection, or an address enqueue_campaign_send caught as malformed/
        placeholder before ever calling Resend) — never touches "sent" rows
        or recomputes the recipient list from Guardians. A blanket
        reset-and-resend would double-send anyone who already succeeded,
        which is exactly what the pending-only re-query in
        BulkEmailBatchSendView/enqueue_campaign_send is designed to prevent
        elsewhere. A campaign can carry both "sent" and "failed" rows and
        still show overall status "sent" (see bulk_email.finalize_campaign —
        status is only "failed" when nothing succeeded at all), so this
        checks the retriable count directly rather than gating on
        campaign.status.

        Also re-pulls each retried row's `email` from the live Guardian
        record before retrying. EmailCampaignRecipient.email is normally a
        frozen snapshot (see the model's docstring) so a "sent" record stays
        an accurate historical log even if Guardian data changes later — but
        neither a "failed" nor a "skipped_invalid" row was ever actually
        delivered, so there's no history to protect, and the whole point of
        retrying is usually that staff just corrected a bad address on the
        Guardian. Without this, editing the Guardian wouldn't change what a
        retry actually sends to. A still-bad address just gets marked
        skipped_invalid again by enqueue_campaign_send — retrying is always
        safe to click, never re-sends something that already succeeded."""
        if not request.user.has_perm("admissions.can_send_bulk_email"):
            self.message_user(request, "You don't have permission to send bulk email.", level=messages.ERROR)
            return

        for campaign in queryset:
            retriable = campaign.recipients.filter(status__in=["failed", "skipped_invalid"])
            retriable_count = retriable.count()
            if retriable_count == 0:
                self.message_user(
                    request, f'"{campaign.name}" has no failed or skipped recipients — nothing to retry.',
                    level=messages.WARNING,
                )
                continue

            with transaction.atomic():
                retriable_rows = list(retriable.select_related("guardian"))
                for row in retriable_rows:
                    row.email = row.guardian.email
                    row.status = "pending"
                    row.error_message = ""
                EmailCampaignRecipient.objects.bulk_update(retriable_rows, ["email", "status", "error_message"])
                counts = campaign.recipients.aggregate(
                    sent=Count("id", filter=Q(status="sent")),
                    failed=Count("id", filter=Q(status="failed")),
                    skipped=Count("id", filter=Q(status="skipped_invalid")),
                )
                campaign.sent_count = counts["sent"]
                campaign.failed_count = counts["failed"]
                campaign.skipped_count = counts["skipped"]
                campaign.status = "queued"
                campaign.save(update_fields=["sent_count", "failed_count", "skipped_count", "status"])

            try:
                task_count, skipped_count = bulk_email.enqueue_campaign_send(campaign)
            except Exception:
                logger.exception("Failed to enqueue retry for campaign %s", campaign.id)
                EmailCampaignRecipient.objects.filter(campaign=campaign, status="pending").update(
                    status="failed", error_message="Retry failed to queue — a background-job error occurred.",
                )
                counts = campaign.recipients.aggregate(
                    sent=Count("id", filter=Q(status="sent")),
                    failed=Count("id", filter=Q(status="failed")),
                    skipped=Count("id", filter=Q(status="skipped_invalid")),
                )
                campaign.sent_count = counts["sent"]
                campaign.failed_count = counts["failed"]
                campaign.skipped_count = counts["skipped"]
                campaign.status = "sent" if counts["sent"] > 0 else "failed"
                campaign.save(update_fields=["sent_count", "failed_count", "skipped_count", "status"])
                self.message_user(
                    request,
                    f'"{campaign.name}": could not queue the retry (a background-job error) — recipients reverted to Failed. Try again once the issue is resolved.',
                    level=messages.ERROR,
                )
                continue

            retried_count = retriable_count - skipped_count
            if retried_count == 0:
                self.message_user(
                    request,
                    f'"{campaign.name}": all {skipped_count} retried recipient(s) still had an invalid/placeholder '
                    f'address — none were sent to Resend. Fix the address on the Guardian record and retry again.',
                    level=messages.ERROR,
                )
            elif skipped_count:
                self.message_user(
                    request,
                    f'"{campaign.name}" retry queued: {retried_count} recipient(s) across {task_count} batch(es) '
                    f'— {skipped_count} still invalid, not retried.',
                    level=messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    f'"{campaign.name}" retry queued: {retried_count} recipient(s) across {task_count} batch(es).',
                )
