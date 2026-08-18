from django.db import models


class Family(models.Model):
    """A prospective or enrolled family. The root of the admissions data model —
    everything (guardians, students, applications) hangs off a Family, not the
    other way around. See docs/admissions/01-vision.md."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        guardians = ", ".join(g.full_name for g in self.guardians.all()[:2])
        return guardians or f"Family #{self.pk}"


class Guardian(models.Model):
    RELATIONSHIP_CHOICES = [
        ("mother", "Mother"),
        ("father", "Father"),
        ("guardian", "Guardian"),
        ("other", "Other"),
    ]

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="guardians")
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)

    def __str__(self):
        return self.full_name


class Student(models.Model):
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="students")
    full_name = models.CharField(max_length=255)
    date_of_birth = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.full_name


class Application(models.Model):
    STAGE_CHOICES = [
        ("inquiry", "Inquiry"),
        ("application", "Application"),
        ("document_review", "Document Review"),
        ("offer", "Offer"),
        ("enrolled", "Enrolled"),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="applications")
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default="inquiry")
    academic_year = models.CharField(max_length=9, help_text="e.g. 2026/2027")
    year_group_applied_for = models.CharField(max_length=50, help_text="e.g. Grade 1, KG2")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student.full_name} — {self.year_group_applied_for} ({self.get_stage_display()})"


class Document(models.Model):
    TYPE_CHOICES = [
        ("proof_of_funds", "Proof of Funds"),
        ("previous_report", "Previous School Report"),
        ("passport_photo", "Passport Photograph"),
        ("birth_certificate", "Birth Certificate"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("required", "Required"),
        ("uploaded", "Uploaded"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    file_url = models.URLField(blank=True, help_text="URL of the file in Supabase Storage")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="required")
    uploaded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.application}"


class Note(models.Model):
    """Internal, staff-only notes on an application. Never shown to parents."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Note on {self.application} ({self.created_at:%Y-%m-%d})"
