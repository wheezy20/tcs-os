from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Family, Guardian, Student, Application, Document, Note


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


class NoteInline(TabularInline):
    model = Note
    extra = 0
    readonly_fields = ("author", "created_at")


@admin.register(Application)
class ApplicationAdmin(ModelAdmin):
    inlines = [DocumentInline, NoteInline]
    list_display = ("student", "year_group_applied_for", "academic_year", "month_of_enrollment", "stage", "updated_at")
    list_filter = ("stage", "academic_year", "year_group_applied_for")
    search_fields = ("student__full_name",)


@admin.register(Student)
class StudentAdmin(ModelAdmin):
    list_display = ("full_name", "family", "date_of_birth", "current_grade", "current_school")
    search_fields = ("full_name",)


@admin.register(Guardian)
class GuardianAdmin(ModelAdmin):
    list_display = ("full_name", "family", "relationship", "email", "phone", "town_city")
    search_fields = ("first_name", "surname", "email")
