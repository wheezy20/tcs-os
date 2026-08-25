from django.core.validators import RegexValidator
from rest_framework import serializers
from .models import Family, Guardian, Student, Application, Document

# Grades where TCS requires proof of vaccination on a formal Application.
# Checked against the grade being applied for, not the child's current grade.
PRESCHOOL_GRADES = {"Pre Nursery", "Nursery 1", "Nursery 2", "Kindergarten 1", "Kindergarten 2"}

# The 3 document types collected on the public Application form. Document.TYPE_CHOICES
# has more (proof_of_funds, passport_photo, birth_certificate, other) reserved for later phases.
APPLICATION_DOCUMENT_TYPES = {"proof_of_vaccination", "financial_clearance", "previous_report"}
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
    student = InquiryStudentSerializer()
    documents = ApplicationDocumentSerializer(many=True, required=False)

    def validate(self, data):
        student_data = data.get("student", {})
        documents_data = data.get("documents", [])
        if student_data.get("year_group_applied_for") in PRESCHOOL_GRADES:
            has_vaccination_proof = any(
                doc["document_type"] == "proof_of_vaccination" for doc in documents_data
            )
            if not has_vaccination_proof:
                raise serializers.ValidationError(
                    {"documents": "Proof of vaccination is required for preschool applicants."}
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
        }

    def create(self, validated_data):
        guardians_data = validated_data["guardians"]
        student_data = validated_data["student"]
        documents_data = validated_data.get("documents", [])

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

        student = family.students.filter(
            full_name__iexact=student_data["full_name"],
            date_of_birth=student_data["date_of_birth"],
        ).first()
        if student:
            student.current_school = student_data["current_school"]
            student.current_grade = student_data["current_grade"]
            student.save()
        else:
            student = Student.objects.create(
                family=family,
                full_name=student_data["full_name"],
                date_of_birth=student_data["date_of_birth"],
                current_school=student_data["current_school"],
                current_grade=student_data["current_grade"],
            )

        application = student.applications.filter(
            academic_year=student_data["academic_year"],
            year_group_applied_for=student_data["year_group_applied_for"],
        ).first()
        if application:
            application.stage = "application"
            application.month_of_enrollment = student_data["month_of_enrollment"]
            application.save()
        else:
            application = Application.objects.create(
                student=student,
                stage="application",
                academic_year=student_data["academic_year"],
                year_group_applied_for=student_data["year_group_applied_for"],
                month_of_enrollment=student_data["month_of_enrollment"],
            )

        for doc in documents_data:
            Document.objects.create(
                application=application,
                document_type=doc["document_type"],
                file_path=doc["file_path"],
                status="pending_review",
            )

        return application


class UploadURLRequestSerializer(serializers.Serializer):
    """POST /api/admissions/upload-url/ — mints a signed Supabase Storage
    upload URL for one document. document_type is restricted to the types
    actually collected on the Application form."""

    document_type = serializers.ChoiceField(choices=_APPLICATION_DOCUMENT_CHOICES)
    filename = serializers.CharField(max_length=255)


class OfferResponseSerializer(serializers.Serializer):
    """POST /api/admissions/offers/<token>/respond/ — the only field a parent
    can set on an Offer they didn't generate themselves. 'accepted'/'declined'
    only; 'pending'/'expired' aren't valid things for a parent to submit."""

    response = serializers.ChoiceField(choices=[("accepted", "Accepted"), ("declined", "Declined")])
