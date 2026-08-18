from django.contrib import admin
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from . import storage
from .models import Family, Guardian, Student, Application, Document, Note, ReferenceCounter


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


@admin.register(Application)
class ApplicationAdmin(ModelAdmin):
    inlines = [DocumentInline, NoteInline]
    list_display = (
        "student", "year_group_applied_for", "academic_year", "month_of_enrollment",
        "stage", "inquiry_reference", "application_reference", "updated_at",
    )
    list_filter = ("stage", "academic_year", "year_group_applied_for")
    search_fields = ("student__full_name", "inquiry_reference", "application_reference")
    readonly_fields = ("inquiry_reference", "application_reference")
    actions = ["mark_as_application", "mark_as_document_review", "mark_as_offer", "mark_as_enrolled"]

    def save_formset(self, request, form, formset, change):
        """NoteInline marks 'author' readonly, but nothing else was setting it —
        every note would otherwise be saved with author=NULL. Assign the
        logged-in staff user on new notes here."""
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if isinstance(instance, Note) and not instance.pk:
                instance.author = request.user
            instance.save()
        formset.save_m2m()

    def _bulk_set_stage(self, request, queryset, stage, label):
        """queryset.update() would be a raw bulk SQL UPDATE that bypasses
        Application.save() entirely — and reference-number/student_id
        assignment lives in that save() override. Must save() each instance
        individually so those side effects actually fire."""
        count = 0
        for application in queryset:
            application.stage = stage
            application.save()
            count += 1
        self.message_user(request, f"{count} application(s) moved to {label} stage.")

    @admin.action(description="Move selected to: Application")
    def mark_as_application(self, request, queryset):
        self._bulk_set_stage(request, queryset, "application", "Application")

    @admin.action(description="Move selected to: Document Review")
    def mark_as_document_review(self, request, queryset):
        self._bulk_set_stage(request, queryset, "document_review", "Document Review")

    @admin.action(description="Move selected to: Offer")
    def mark_as_offer(self, request, queryset):
        self._bulk_set_stage(request, queryset, "offer", "Offer")

    @admin.action(description="Move selected to: Enrolled")
    def mark_as_enrolled(self, request, queryset):
        self._bulk_set_stage(request, queryset, "enrolled", "Enrolled")


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
