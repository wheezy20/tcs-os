from rest_framework import serializers
from .models import Family, Guardian, Student, Application, Document


class InquiryDocumentSerializer(serializers.Serializer):
    """Accepts a file already uploaded to Supabase Storage by the frontend —
    Django never receives the raw file, only the resulting URL. See
    docs/shared-stack.md and docs/admissions/02-stack-and-schema.md."""
    document_type = serializers.ChoiceField(choices=Document.TYPE_CHOICES)
    file_url = serializers.URLField()


class InquirySerializer(serializers.Serializer):
    """Public-facing serializer for POST /api/admissions/inquiries/.
    Creates a Family + Guardian + Student + Application (stage='inquiry') in one call."""

    guardian_full_name = serializers.CharField(max_length=255)
    guardian_email = serializers.EmailField()
    guardian_phone = serializers.CharField(max_length=32)
    guardian_relationship = serializers.ChoiceField(choices=Guardian.RELATIONSHIP_CHOICES)

    student_full_name = serializers.CharField(max_length=255)
    student_date_of_birth = serializers.DateField(required=False, allow_null=True)

    academic_year = serializers.CharField(max_length=9)
    year_group_applied_for = serializers.CharField(max_length=50)

    documents = InquiryDocumentSerializer(many=True, required=False)

    def to_representation(self, instance):
        """The input fields above (guardian_full_name, etc.) don't exist on the
        Application model, so DRF can't use them to render the response.
        Return a simple confirmation payload instead."""
        return {
            "id": instance.pk,
            "stage": instance.stage,
            "student_full_name": instance.student.full_name,
            "year_group_applied_for": instance.year_group_applied_for,
            "academic_year": instance.academic_year,
        }

    def create(self, validated_data):
        documents_data = validated_data.pop("documents", [])

        family = Family.objects.create()
        Guardian.objects.create(
            family=family,
            full_name=validated_data["guardian_full_name"],
            email=validated_data["guardian_email"],
            phone=validated_data["guardian_phone"],
            relationship=validated_data["guardian_relationship"],
        )
        student = Student.objects.create(
            family=family,
            full_name=validated_data["student_full_name"],
            date_of_birth=validated_data.get("student_date_of_birth"),
        )
        application = Application.objects.create(
            student=student,
            stage="inquiry",
            academic_year=validated_data["academic_year"],
            year_group_applied_for=validated_data["year_group_applied_for"],
        )
        for doc in documents_data:
            Document.objects.create(
                application=application,
                document_type=doc["document_type"],
                file_url=doc["file_url"],
                status="uploaded",
            )
        return application
