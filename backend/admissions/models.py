import logging

from django.db import models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Student ID classification prefix, keyed by the grade a student enrolled at
# (year_group_applied_for on the Application that reached stage="enrolled") —
# see docs/admissions/02-stack-and-schema.md for the full numbering scheme.
#
# TODO(SHS): TCS doesn't offer Grade 10-12 (Senior High School) yet, so there's
# deliberately no classification code for it. If a student is ever enrolled at
# one of those grades before TCS confirms an SHS numbering convention, DO NOT
# guess a code — Student.student_id is left unassigned (see
# _assign_student_id_if_needed below) and a warning is logged so it surfaces
# as a visibly-missing field in admin rather than a silently wrong one.
STUDENT_ID_CLASSIFICATION = {
    "Pre Nursery": "01",
    "Nursery 1": "01",
    "Nursery 2": "01",
    "Kindergarten 1": "02",
    "Kindergarten 2": "02",
    "Grade 1": "03",
    "Grade 2": "03",
    "Grade 3": "03",
    "Grade 4": "03",
    "Grade 5": "03",
    "Grade 6": "03",
    "Grade 7": "04",
    "Grade 8": "04",
    "Grade 9": "04",
}


class ReferenceCounter(models.Model):
    """Backs the sequential portion of every human-readable reference number
    (Inquiry/Application reference numbers, Student ID roll numbers). One row
    per (kind, year[, classification]) scope — e.g. key="INQ-2026" or
    key="STUDENT-26-01". select_for_update() makes concurrent increments of
    the same key safe: two simultaneous submissions can't be handed the same
    sequence number, since the second transaction blocks on the row lock
    until the first commits. See docs/admissions/02-stack-and-schema.md."""

    key = models.CharField(max_length=50, unique=True)
    next_value = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.key} → {self.next_value}"

    @classmethod
    def next_for(cls, key):
        with transaction.atomic():
            cls.objects.get_or_create(key=key, defaults={"next_value": 1})
            counter = cls.objects.select_for_update().get(key=key)
            value = counter.next_value
            counter.next_value = value + 1
            counter.save(update_fields=["next_value"])
            return value


def _next_inquiry_reference():
    year = timezone.now().year
    seq = ReferenceCounter.next_for(f"INQ-{year}")
    return f"INQ-{year}-{seq:04d}"


def _next_application_reference():
    year = timezone.now().year
    seq = ReferenceCounter.next_for(f"APP-{year}")
    return f"APP-{year}-{seq:04d}"


def _assign_student_id_if_needed(application):
    """Called from Application.save() when stage='enrolled'. Assigns
    Student.student_id exactly once per student — a no-op if the student
    already has one (e.g. a returning family re-applying keeps their
    original ID, per the Phase 2 dedup-matching logic)."""
    student = application.student
    if student.student_id:
        return

    classification = STUDENT_ID_CLASSIFICATION.get(application.year_group_applied_for)
    if classification is None:
        logger.warning(
            "Could not assign student_id for Application #%s (student %r): grade %r has "
            "no Student ID classification code — see STUDENT_ID_CLASSIFICATION in models.py. "
            "Leaving student_id unset; needs manual admin attention.",
            application.pk, student.full_name, application.year_group_applied_for,
        )
        return

    yy = timezone.now().year % 100
    seq = ReferenceCounter.next_for(f"STUDENT-{yy:02d}-{classification}")
    student.student_id = f"{yy:02d}{classification}{seq:04d}"
    student.save(update_fields=["student_id"])


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
    student_id = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        help_text="Permanent ID, format YYPPNNNN (year + classification + roll number). "
        "Assigned once, the first time any of this student's Applications reaches "
        "stage='enrolled' — see Application.save() and STUDENT_ID_CLASSIFICATION.",
    )

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
    inquiry_reference = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        help_text="Format INQ-YYYY-NNNN. Assigned once, only if this row was created at "
        "stage='inquiry' — never backfilled for rows that skip straight to 'application'.",
    )
    application_reference = models.CharField(
        max_length=20, unique=True, null=True, blank=True,
        help_text="Format APP-YYYY-NNNN. Assigned once, the first time this row's stage "
        "reaches 'application' or later (whether created there directly or advanced from "
        "an inquiry) — never reassigned as it moves further through the pipeline.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student.full_name} — {self.year_group_applied_for} ({self.get_stage_display()})"

    def save(self, *args, **kwargs):
        stage_order = [choice[0] for choice in self.STAGE_CHOICES]
        is_new = self.pk is None

        with transaction.atomic():
            if is_new and self.stage == "inquiry" and not self.inquiry_reference:
                self.inquiry_reference = _next_inquiry_reference()

            if (
                stage_order.index(self.stage) >= stage_order.index("application")
                and not self.application_reference
            ):
                self.application_reference = _next_application_reference()

            super().save(*args, **kwargs)

            if self.stage == "enrolled":
                _assign_student_id_if_needed(self)


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
