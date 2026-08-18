from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin, StackedInline, TabularInline

from . import emails, storage
from .models import (
    Application, Capacity, Decision, Document, Family, Guardian, Note,
    Offer, ReferenceCounter, Student,
)


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


@admin.register(Application)
class ApplicationAdmin(ModelAdmin):
    inlines = [DecisionInline, OfferInline, DocumentInline, NoteInline]
    list_display = (
        "student", "year_group_applied_for", "academic_year", "month_of_enrollment",
        "stage", "inquiry_reference", "application_reference", "updated_at",
    )
    list_filter = ("stage", "academic_year", "year_group_applied_for")
    search_fields = ("student__full_name", "inquiry_reference", "application_reference")
    readonly_fields = ("inquiry_reference", "application_reference")
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
        formset.save_m2m()

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
    list_display = ("full_name", "family", "date_of_birth", "current_grade", "current_school", "student_id")
    search_fields = ("full_name", "student_id")
    readonly_fields = ("student_id",)


@admin.register(Guardian)
class GuardianAdmin(ModelAdmin):
    list_display = ("full_name", "family", "relationship", "email", "phone", "town_city")
    search_fields = ("first_name", "surname", "email")


@admin.register(ReferenceCounter)
class ReferenceCounterAdmin(ModelAdmin):
    """Visibility/debugging only — these rows are maintained entirely by
    ReferenceCounter.next_for(), never hand-edited in normal operation."""
    list_display = ("key", "next_value")
    search_fields = ("key",)


@admin.register(Capacity)
class CapacityAdmin(ModelAdmin):
    list_display = ("year_group", "academic_year", "capacity")
    list_filter = ("academic_year",)
    search_fields = ("year_group",)
