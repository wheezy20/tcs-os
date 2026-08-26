from django.conf import settings
from django.core.validators import RegexValidator
from django.utils import timezone
from rest_framework import serializers
from .models import (
    Campus, EmergencyContact, Family, Guardian, HealthInfo, Student, Application, Document,
)
from .storage import ALLOWED_UPLOAD_EXTENSIONS

MAX_UPLOAD_SIZE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

# Grades where TCS requires proof of vaccination on a formal Application.
# Checked against the grade being applied for, not the child's current grade.
PRESCHOOL_GRADES = {"Pre Nursery", "Nursery 1", "Nursery 2", "Kindergarten 1", "Kindergarten 2"}

# Annex campus only accepts these grades — checked by Campus.name (see
# Campus's own docstring for the tradeoff of matching by name).
ANNEX_ACCEPTED_GRADES = {"Pre Nursery", "Nursery 1"}

# The document types collected on the public Application form. Document.TYPE_CHOICES
# has more (proof_of_funds, passport_photo, birth_certificate, other) reserved for later phases.
APPLICATION_DOCUMENT_TYPES = {"proof_of_vaccination", "financial_clearance", "previous_report", "application_fee_proof"}
_APPLICATION_DOCUMENT_CHOICES = [c for c in Document.TYPE_CHOICES if c[0] in APPLICATION_DOCUMENT_TYPES]

# "+" + 3-digit country code + exactly 9 digits, e.g. +233551794822. Matches
# the frontend's client-side check (index.html/application.html) — kept here
# too since the frontend check is only ever a convenience, not the guarantee.
phone_validator = RegexValidator(
    regex=r"^\+\d{3}\d{9}$",
    message="Enter a valid phone number, e.g. +233551794822 (country code, then 9 digits, no spaces).",
)


class InquiryGuardianSerializer(serializers.Serializer):
    surname = serializers.CharField(max_length=255)
    first_name = serializers.CharField(max_length=255)
    relationship = serializers.ChoiceField(choices=Guardian.RELATIONSHIP_CHOICES)
    religion = serializers.CharField(max_length=255)
    address = serializers.CharField(max_length=255)
    town_city = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=32, validators=[phone_validator])
    email = serializers.EmailField()


class InquiryStudentSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    date_of_birth = serializers.DateField()
    current_school = serializers.CharField(max_length=255)
    current_grade = serializers.CharField(max_length=50)
    year_group_applied_for = serializers.CharField(max_length=50)
    academic_year = serializers.CharField(max_length=50)
    month_of_enrollment = serializers.CharField(max_length=50)


class InquirySerializer(serializers.Serializer):
    """Public-facing serializer for POST /api/admissions/inquiries/.
    Creates a Family + 1-2 Guardians + 1-5 Students, each with its own
    Application (stage='inquiry'), in one call. Academic year and month of
    enrollment are per-student, so siblings can enquire for different
    intakes in the same submission."""

    referral_source = serializers.ChoiceField(choices=Family.REFERRAL_SOURCE_CHOICES)
    referral_source_other = serializers.CharField(max_length=255, required=False, allow_blank=True)

    guardians = InquiryGuardianSerializer(many=True, min_length=1, max_length=2)
    students = InquiryStudentSerializer(many=True, min_length=1, max_length=5)

    comments = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if data.get("referral_source") == "other" and not data.get("referral_source_other"):
            raise serializers.ValidationError(
                {"referral_source_other": "Please tell us how you heard about us."}
            )
        return data

    def to_representation(self, instance):
        """The input fields above don't exist on the Family model, so DRF
        can't use them to render the response. Return a simple confirmation
        payload instead. `instance` is the Family returned by create()."""
        return {
            "family_id": instance.pk,
            "applications": [
                {
                    "id": application.pk,
                    "reference": application.inquiry_reference,
                    "stage": application.stage,
                    "student_full_name": application.student.full_name,
                    "year_group_applied_for": application.year_group_applied_for,
                    "academic_year": application.academic_year,
                }
                for application in instance.created_applications
            ],
        }

    def create(self, validated_data):
        guardians_data = validated_data.pop("guardians")
        students_data = validated_data.pop("students")

        family = Family.objects.create(
            referral_source=validated_data["referral_source"],
            referral_source_other=validated_data.get("referral_source_other", ""),
            comments=validated_data.get("comments", ""),
        )

        for guardian_data in guardians_data:
            Guardian.objects.create(family=family, **guardian_data)

        created_applications = []
        for student_data in students_data:
            student = Student.objects.create(
                family=family,
                full_name=student_data["full_name"],
                date_of_birth=student_data["date_of_birth"],
                current_school=student_data["current_school"],
                current_grade=student_data["current_grade"],
            )
            application = Application.objects.create(
                student=student,
                stage="inquiry",
                academic_year=student_data["academic_year"],
                year_group_applied_for=student_data["year_group_applied_for"],
                month_of_enrollment=student_data["month_of_enrollment"],
            )
            created_applications.append(application)

        family.created_applications = created_applications
        return family


class ApplicationDocumentSerializer(serializers.Serializer):
    document_type = serializers.ChoiceField(choices=_APPLICATION_DOCUMENT_CHOICES)
    file_path = serializers.CharField(max_length=500)


class ApplicationStudentSerializer(serializers.Serializer):
    """Expanded student sub-payload for the Application form specifically —
    Inquiry stays on the lighter InquiryStudentSerializer above, deliberately
    not sharing this one, since Inquiry is meant to stay a quick first-touch
    form. address/town_city are collected on Student here (see the model's
    own comment on why, distinct from Guardian's address) — the frontend's
    "same as guardian" checkbox is a pure client-side value copy, same
    pattern as the existing guardian-2 checkbox, so the server just receives
    whatever ends up in these fields either way."""

    full_name = serializers.CharField(max_length=255)
    date_of_birth = serializers.DateField()
    gender = serializers.ChoiceField(choices=Student.GENDER_CHOICES)
    nationality = serializers.CharField(max_length=100)
    address = serializers.CharField(max_length=255)
    town_city = serializers.CharField(max_length=255)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    country = serializers.CharField(max_length=100, required=False, allow_blank=True)
    current_school = serializers.CharField(max_length=255)
    previous_school_location = serializers.CharField(max_length=255, required=False, allow_blank=True)
    current_grade = serializers.CharField(max_length=50)
    year_group_applied_for = serializers.CharField(max_length=50)
    academic_year = serializers.CharField(max_length=50)
    month_of_enrollment = serializers.CharField(max_length=50)


class EmergencyContactSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    relationship = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=32, validators=[phone_validator])


class HealthInfoSerializer(serializers.Serializer):
    has_learning_or_physical_needs = serializers.BooleanField(default=False)
    learning_or_physical_needs_details = serializers.CharField(required=False, allow_blank=True)
    has_medical_conditions = serializers.BooleanField(default=False)
    medical_conditions_details = serializers.CharField(required=False, allow_blank=True)
    has_allergies_or_dietary_requirements = serializers.BooleanField(default=False)
    allergies_dietary_details = serializers.CharField(required=False, allow_blank=True)


class ApplicationSerializer(serializers.Serializer):
    """Public-facing serializer for POST /api/admissions/applications/. Unlike
    InquirySerializer this is single-child — an Application is inherently
    per-student. Open to anyone, not gated behind a prior Inquiry: matches an
    existing Family/Guardian/Student (by guardian email, then student
    name+DOB) and reuses it rather than creating a duplicate, and advances an
    existing inquiry-stage Application to 'application' rather than creating
    a second row for the same student/year/grade. See
    docs/admissions/02-stack-and-schema.md for the matching rules and their
    known limitations."""

    guardians = InquiryGuardianSerializer(many=True, min_length=1, max_length=2)
    student = ApplicationStudentSerializer()
    documents = ApplicationDocumentSerializer(many=True, required=False)
    # By name, not PK — the frontend's campus options are two hardcoded static
    # values ("Main"/"Annex", matching the dependency-free no-build-step
    # pattern the rest of these forms use), not fetched from a live endpoint,
    # so it only ever knows Campus by name, never a DB id.
    campus = serializers.SlugRelatedField(slug_field="name", queryset=Campus.objects.all())
    emergency_contact = EmergencyContactSerializer()
    health_info = HealthInfoSerializer()
    wants_scholarship_info = serializers.BooleanField(default=False)
    scholarship_interest_details = serializers.CharField(required=False, allow_blank=True)
    declaration_signature_name = serializers.CharField(max_length=255)
    declaration_agreed = serializers.BooleanField()
    media_consent_agreed = serializers.BooleanField(default=False)

    def validate(self, data):
        student_data = data.get("student", {})
        documents_data = data.get("documents", [])
        campus = data.get("campus")

        if student_data.get("year_group_applied_for") in PRESCHOOL_GRADES:
            has_vaccination_proof = any(
                doc["document_type"] == "proof_of_vaccination" for doc in documents_data
            )
            if not has_vaccination_proof:
                raise serializers.ValidationError(
                    {"documents": "Proof of vaccination is required for preschool applicants."}
                )

        if campus and campus.name == "Annex" and student_data.get("year_group_applied_for") not in ANNEX_ACCEPTED_GRADES:
            raise serializers.ValidationError(
                {"campus": f"Annex campus only accepts: {', '.join(sorted(ANNEX_ACCEPTED_GRADES))}."}
            )

        if not data.get("declaration_agreed"):
            raise serializers.ValidationError(
                {"declaration_agreed": "You must agree to the declaration to submit this application."}
            )

        return data

    def to_representation(self, instance):
        """`instance` is the Application returned by create()."""
        return {
            "application_id": instance.pk,
            "reference": instance.application_reference,
            "family_id": instance.student.family_id,
            "stage": instance.stage,
            "student_full_name": instance.student.full_name,
            "year_group_applied_for": instance.year_group_applied_for,
            "academic_year": instance.academic_year,
            "student_id": instance.student.student_id,
            "campus": instance.campus.name if instance.campus else None,
        }

    def create(self, validated_data):
        guardians_data = validated_data["guardians"]
        student_data = validated_data["student"]
        documents_data = validated_data.get("documents", [])
        emergency_contact_data = validated_data["emergency_contact"]
        health_info_data = validated_data["health_info"]

        family = None
        for guardian_data in guardians_data:
            existing_guardian = (
                Guardian.objects.filter(email__iexact=guardian_data["email"])
                .select_related("family")
                .first()
            )
            if existing_guardian:
                family = existing_guardian.family
                break
        if family is None:
            family = Family.objects.create()

        for guardian_data in guardians_data:
            guardian = family.guardians.filter(email__iexact=guardian_data["email"]).first()
            if guardian:
                for field, value in guardian_data.items():
                    setattr(guardian, field, value)
                guardian.save()
            else:
                Guardian.objects.create(family=family, **guardian_data)

        student_fields = {
            "current_school": student_data["current_school"],
            "current_grade": student_data["current_grade"],
            "gender": student_data["gender"],
            "nationality": student_data["nationality"],
            "address": student_data["address"],
            "town_city": student_data["town_city"],
            "postal_code": student_data.get("postal_code", ""),
            "country": student_data.get("country", ""),
            "previous_school_location": student_data.get("previous_school_location", ""),
        }
        student = family.students.filter(
            full_name__iexact=student_data["full_name"],
            date_of_birth=student_data["date_of_birth"],
        ).first()
        if student:
            for field, value in student_fields.items():
                setattr(student, field, value)
            student.save()
        else:
            student = Student.objects.create(
                family=family,
                full_name=student_data["full_name"],
                date_of_birth=student_data["date_of_birth"],
                **student_fields,
            )

        application = student.applications.filter(
            academic_year=student_data["academic_year"],
            year_group_applied_for=student_data["year_group_applied_for"],
        ).first()
        request = self.context.get("request")
        # Best-effort audit trail, not a verified identity — see
        # Application.declaration_ip_address's own comment on REMOTE_ADDR
        # potentially reflecting a proxy hop rather than the parent's real IP.
        ip_address = request.META.get("REMOTE_ADDR") if request else None
        application_fields = {
            "campus": validated_data["campus"],
            "wants_scholarship_info": validated_data.get("wants_scholarship_info", False),
            "scholarship_interest_details": validated_data.get("scholarship_interest_details", ""),
            "declaration_signature_name": validated_data["declaration_signature_name"],
            "declaration_agreed": validated_data["declaration_agreed"],
            "declaration_agreed_at": timezone.now(),
            "declaration_ip_address": ip_address,
            "media_consent_agreed": validated_data.get("media_consent_agreed", False),
        }
        if application:
            application.stage = "application"
            application.month_of_enrollment = student_data["month_of_enrollment"]
            for field, value in application_fields.items():
                setattr(application, field, value)
            application.save()
        else:
            application = Application.objects.create(
                student=student,
                stage="application",
                academic_year=student_data["academic_year"],
                year_group_applied_for=student_data["year_group_applied_for"],
                month_of_enrollment=student_data["month_of_enrollment"],
                **application_fields,
            )

        for doc in documents_data:
            Document.objects.create(
                application=application,
                document_type=doc["document_type"],
                file_path=doc["file_path"],
                status="pending_review",
            )

        # A plain FK (not OneToOne) so the model can hold more than one later,
        # but the form only ever collects one today — update the existing
        # one on a re-submission rather than piling up duplicates.
        existing_contact = application.emergency_contacts.first()
        if existing_contact:
            for field, value in emergency_contact_data.items():
                setattr(existing_contact, field, value)
            existing_contact.save()
        else:
            EmergencyContact.objects.create(application=application, **emergency_contact_data)

        HealthInfo.objects.update_or_create(
            application=application,
            defaults={
                "has_learning_or_physical_needs": health_info_data.get("has_learning_or_physical_needs", False),
                "learning_or_physical_needs_details": health_info_data.get("learning_or_physical_needs_details", ""),
                "has_medical_conditions": health_info_data.get("has_medical_conditions", False),
                "medical_conditions_details": health_info_data.get("medical_conditions_details", ""),
                "has_allergies_or_dietary_requirements": health_info_data.get(
                    "has_allergies_or_dietary_requirements", False
                ),
                "allergies_dietary_details": health_info_data.get("allergies_dietary_details", ""),
            },
        )

        return application


class UploadURLRequestSerializer(serializers.Serializer):
    """POST /api/admissions/upload-url/ — mints a signed Supabase Storage
    upload URL for one document. document_type is restricted to the types
    actually collected on the Application form.

    file_size is client-declared, so this is a fast-fail convenience, not the
    real security boundary — a client could lie here. The bucket's own
    file_size_limit (set via `manage.py configure_storage_bucket`) is what
    actually rejects an oversized PUT, since it checks the real bytes."""

    document_type = serializers.ChoiceField(choices=_APPLICATION_DOCUMENT_CHOICES)
    filename = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField(min_value=1)

    def validate_filename(self, value):
        extension = value.rsplit(".", 1)[-1].lower() if "." in value else ""
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
            raise serializers.ValidationError(f"Unsupported file type. Allowed types: {allowed}.")
        return value

    def validate_file_size(self, value):
        if value > MAX_UPLOAD_SIZE_BYTES:
            raise serializers.ValidationError(
                f"File is too large. Maximum size is {settings.MAX_UPLOAD_SIZE_MB}MB."
            )
        return value


class ApplicationDraftSaveSerializer(serializers.Serializer):
    """POST /api/admissions/application-drafts/ (new draft) and
    PATCH .../<token>/ (save progress). `data` is intentionally NOT validated
    against ApplicationSerializer here — a draft can be genuinely
    incomplete/invalid at any point in the multi-step form. Full validation
    only ever happens once, at final submit, against the real
    ApplicationSerializer — one source of truth for what "valid" means."""

    email = serializers.EmailField(required=False, allow_blank=True)
    data = serializers.JSONField()
    current_step = serializers.IntegerField(min_value=0, default=0)


class OfferResponseSerializer(serializers.Serializer):
    """POST /api/admissions/offers/<token>/respond/ — the only field a parent
    can set on an Offer they didn't generate themselves. 'accepted'/'declined'
    only; 'pending'/'expired' aren't valid things for a parent to submit."""

    response = serializers.ChoiceField(choices=[("accepted", "Accepted"), ("declined", "Declined")])
