from rest_framework import serializers
from .models import Family, Guardian, Student, Application


class InquiryGuardianSerializer(serializers.Serializer):
    surname = serializers.CharField(max_length=255)
    first_name = serializers.CharField(max_length=255)
    relationship = serializers.ChoiceField(choices=Guardian.RELATIONSHIP_CHOICES)
    religion = serializers.CharField(max_length=255)
    address = serializers.CharField(max_length=255)
    town_city = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=32)
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
