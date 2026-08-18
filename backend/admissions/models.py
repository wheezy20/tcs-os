from django.db import models


class Family(models.Model):
    """A prospective or enrolled family. The root of the admissions data model —
    everything (guardians, students, applications) hangs off a Family, not the
    other way around. See docs/admissions/01-vision.md."""

    REFERRAL_SOURCE_CHOICES = [
        ("current_parent", "Current parent of TCS"),
        ("former_parent", "Former parent of TCS"),
        ("parent_referral", "Parent referral"),
        ("staff_referral", "Staff referral"),
        ("website", "Website"),
        ("friend_colleague", "Friend/Colleague"),
        ("social_media", "Social Media"),
        ("other", "Other"),
    ]

    referral_source = models.CharField(max_length=30, choices=REFERRAL_SOURCE_CHOICES, blank=True, default="")
    referral_source_other = models.CharField(
        max_length=255, blank=True, default="", help_text="Set when referral_source is 'other'"
    )
    comments = models.TextField(blank=True, default="", help_text="Free-text comment/question from the enquiring family")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        guardians = ", ".join(g.full_name for g in self.guardians.all()[:2])
        return guardians or f"Family #{self.pk}"


class Guardian(models.Model):
    RELATIONSHIP_CHOICES = [
        ("mother", "Mother"),
        ("father", "Father"),
        ("guardian", "Legal Guardian"),
        ("other", "Other"),
    ]

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="guardians")
    first_name = models.CharField(max_length=255, default="")
    surname = models.CharField(max_length=255, default="")
    email = models.EmailField()
    phone = models.CharField(max_length=32)
    relationship = models.CharField(max_length=20, choices=RELATIONSHIP_CHOICES)
    religion = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    town_city = models.CharField(max_length=255, blank=True, default="")

    @property
    def full_name(self):
        return f"{self.first_name} {self.surname}".strip()

    def __str__(self):
        return self.full_name


class Student(models.Model):
    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="students")
    full_name = models.CharField(max_length=255)
    date_of_birth = models.DateField(null=True, blank=True)
    current_school = models.CharField(
        max_length=255, blank=True, default="", help_text="e.g. 'N/A' if not yet enrolled anywhere"
    )
    current_grade = models.CharField(max_length=50, blank=True, default="", help_text="Learner's current grade/class")

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
    academic_year = models.CharField(max_length=50, help_text="e.g. 2026")
    year_group_applied_for = models.CharField(max_length=50, help_text="e.g. Grade 1, KG2")
    month_of_enrollment = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student.full_name} — {self.year_group_applied_for} ({self.get_stage_display()})"


class Document(models.Model):
    TYPE_CHOICES = [
        ("proof_of_vaccination", "Proof of Vaccination"),
        ("financial_clearance", "Proof of Financial Clearance from Previous School"),
        ("previous_report", "Report Card / Transcript from Previous School"),
        ("proof_of_funds", "Proof of Funds"),
        ("passport_photo", "Passport Photograph"),
        ("birth_certificate", "Birth Certificate"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("required", "Required"),
        ("pending_review", "Pending Review"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    file_path = models.CharField(
        max_length=500, blank=True,
        help_text="Path within the Supabase Storage bucket — not a URL. The bucket is "
        "private, so viewing a document means minting a fresh signed URL on demand "
        "(see admissions/storage.py) rather than storing one, since a signed URL "
        "created before the file exists would 404, and one stored long-term would "
        "eventually expire silently.",
    )
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
