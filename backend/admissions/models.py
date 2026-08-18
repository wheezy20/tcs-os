import logging
import secrets

from django.core.exceptions import ValidationError
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


def _generate_offer_token():
    return secrets.token_urlsafe(32)


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
        ("waitlisted", "Waitlisted"),
        ("rejected", "Rejected"),
        ("offer_declined", "Offer Declined"),
    ]

    # Stages that require a prior step to have been completed before they can be
    # entered — see _requirement_met_for_stage(). Not a general workflow engine,
    # just the two forward transitions Phase 3 needs to actually gate.
    GATED_STAGES = {"offer", "enrolled"}

    # Any stage that means "this genuinely became a formal application" — including
    # the terminal outcomes, since reaching waitlisted/rejected/offer_declined implies
    # 'application' was passed through at some point. Explicit set rather than an
    # ordinal STAGE_CHOICES-position comparison, since the stage list branches into
    # terminal outcomes now rather than being one strictly linear sequence.
    STAGES_REQUIRING_APPLICATION_REFERENCE = {
        "application", "document_review", "offer", "enrolled",
        "waitlisted", "rejected", "offer_declined",
    }

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

    def _has_accepted_decision(self):
        if not self.pk:
            return False  # a not-yet-saved row can't possibly have a Decision pointing at it
        decision = getattr(self, "decision", None)
        return bool(decision and decision.decision_type == "accepted")

    def _has_accepted_offer(self):
        if not self.pk:
            return False
        offer = getattr(self, "offer", None)
        if not offer:
            return False
        offer.refresh_expiry()  # settle pending -> expired here, before deciding
        return offer.response == "accepted"

    def _requirement_met_for_stage(self, stage):
        if stage == "offer":
            return self._has_accepted_decision()
        if stage == "enrolled":
            return self._has_accepted_decision() and self._has_accepted_offer()
        return True

    def _gate_requirement_description(self, stage):
        if stage == "offer":
            return "requires an accepted Decision first."
        if stage == "enrolled":
            return "requires an accepted Decision and an accepted Offer response."
        return ""

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        previous_stage = None if is_new else (
            Application.objects.filter(pk=self.pk).values_list("stage", flat=True).first()
        )
        entering_gated_stage = self.stage in self.GATED_STAGES and self.stage != previous_stage

        if entering_gated_stage and not self._requirement_met_for_stage(self.stage):
            raise ValidationError(
                f"Cannot move to '{self.get_stage_display()}': "
                f"{self._gate_requirement_description(self.stage)}"
            )

        with transaction.atomic():
            if is_new and self.stage == "inquiry" and not self.inquiry_reference:
                self.inquiry_reference = _next_inquiry_reference()

            if self.stage in self.STAGES_REQUIRING_APPLICATION_REFERENCE and not self.application_reference:
                self.application_reference = _next_application_reference()

            super().save(*args, **kwargs)

            if self.stage == "enrolled":
                _assign_student_id_if_needed(self)


class Decision(models.Model):
    """Records whether an Application was accepted, waitlisted, or rejected.
    One mutable row per Application (not an append-only log) — matches how
    Application.stage itself is a single mutable field, not a history of
    snapshots. Recording a Decision is gated behind the admissions.can_decide
    permission — a plain Django Group/Permission, not a new role/profile
    system. See docs/admissions/02-stack-and-schema.md."""

    DECISION_CHOICES = [
        ("accepted", "Accepted"),
        ("waitlisted", "Waitlisted"),
        ("rejected", "Rejected"),
    ]

    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name="decision")
    decision_type = models.CharField(max_length=20, choices=DECISION_CHOICES)
    decided_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True)
    decided_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        permissions = [("can_decide", "Can make admissions decisions")]

    def __str__(self):
        return f"{self.get_decision_type_display()} — {self.application}"

    def save(self, *args, **kwargs):
        """Negative/terminal outcomes (waitlisted, rejected) propagate onto
        Application.stage automatically, since they're unambiguous and need
        no further action. 'accepted' does NOT auto-advance the stage — it
        only unlocks the offer-stage gate for a deliberate next staff action
        (generating an Offer). See Application._requirement_met_for_stage."""
        is_new = self.pk is None
        previous_type = None if is_new else (
            Decision.objects.filter(pk=self.pk).values_list("decision_type", flat=True).first()
        )
        super().save(*args, **kwargs)

        newly_negative = (
            self.decision_type in ("waitlisted", "rejected") and self.decision_type != previous_type
        )
        if newly_negative:
            # Fetch fresh rather than using self.application: a caller further
            # up the stack (e.g. Application.save()'s own gate check, which
            # runs BEFORE this save() when it's what triggered it — see
            # Offer.save() below for exactly this scenario) may hold an
            # in-memory Application instance whose .stage was already mutated
            # to a not-yet-persisted value. Trusting that cached instance's
            # .stage here would check against a value that was never true in
            # the database.
            application = Application.objects.get(pk=self.application_id)
            if application.stage != "enrolled":
                application.stage = self.decision_type
                application.save()


class Offer(models.Model):
    """A generated admissions offer, communicated to the parent via a signed
    token link (frontend/offer.html). No parent portal exists yet, so the
    unguessable token itself is the access control — same trust model as the
    public Inquiry/Application forms, not a new pattern. Mutable/reusable
    (OneToOneField): re-offering after a decline or expiry means resetting
    this same row (see the admin's "Reset Offer" action), not creating a
    second Offer for the same Application."""

    RESPONSE_CHOICES = [
        ("pending", "Pending"),
        ("accepted", "Accepted"),
        ("declined", "Declined"),
        ("expired", "Expired"),
    ]

    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name="offer")
    token = models.CharField(max_length=64, unique=True, default=_generate_offer_token)
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    response = models.CharField(max_length=20, choices=RESPONSE_CHOICES, default="pending")
    responded_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Offer for {self.application} ({self.get_response_display()})"

    def refresh_expiry(self):
        """No scheduler exists in this project yet (no Celery, no cron,
        nothing deployed), so expiry is resolved lazily here — called
        wherever `response` is about to matter (Application's save() gate,
        the public respond endpoint, admin display) — rather than by a
        timed job that has nowhere to run."""
        if self.response == "pending" and self.expires_at and timezone.now() > self.expires_at:
            self.response = "expired"
            self.save(update_fields=["response"])

    def save(self, *args, **kwargs):
        """Mirrors Decision.save()'s propagation rule: a newly-resolved
        negative outcome (declined or expired) pushes Application.stage to
        'offer_declined' automatically; 'accepted' does not auto-advance to
        'enrolled' — that's still a deliberate staff action, now unblocked
        by the enrolled-stage gate."""
        is_new = self.pk is None
        previous_response = None if is_new else (
            Offer.objects.filter(pk=self.pk).values_list("response", flat=True).first()
        )
        super().save(*args, **kwargs)

        newly_resolved_negative = (
            self.response in ("declined", "expired") and self.response != previous_response
        )
        if newly_resolved_negative:
            # Fetch fresh, not self.application — this save() is frequently
            # called from inside Application.save()'s own gate check (via
            # refresh_expiry(), called from _has_accepted_offer()), which
            # means self.application can be the SAME Python instance the
            # caller already mutated .stage on (e.g. set to "enrolled")
            # before ever calling .save(). Checking a stale in-memory value
            # here would silently skip this propagation — confirmed by a
            # real test failure before this fix.
            application = Application.objects.get(pk=self.application_id)
            if application.stage == "offer":
                application.stage = "offer_declined"
                application.save()


class Capacity(models.Model):
    """Seats available per (academic_year, year_group). ApplicationAdmin
    shows a soft warning (never a hard block — real admissions has
    legitimate reasons to go over on paper: sibling priority, board
    exceptions) when saving a Decision as "accepted" would put the accepted
    count for that (year, grade) over this capacity. See
    ApplicationAdmin._warn_if_over_capacity in admin.py."""

    academic_year = models.CharField(max_length=50)
    year_group = models.CharField(max_length=50)
    capacity = models.PositiveIntegerField()

    class Meta:
        unique_together = ("academic_year", "year_group")
        verbose_name_plural = "capacities"

    def __str__(self):
        return f"{self.year_group} {self.academic_year}: {self.capacity} seats"


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
