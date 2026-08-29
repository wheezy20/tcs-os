import logging
import secrets
from datetime import timedelta

from django.conf import settings
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


def _generate_draft_token():
    return secrets.token_urlsafe(32)


def _generate_unsubscribe_token():
    return secrets.token_urlsafe(32)


def _default_draft_expiry():
    return timezone.now() + timedelta(days=settings.DRAFT_EXPIRY_DAYS)


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

    # Bulk/marketing email only (Phase 6) — never checked by transactional
    # sends (confirmation, offer, draft-resume emails). Generated eagerly for
    # every Guardian, same as Offer.token, so the unsubscribe link in a bulk
    # email is always valid even for a Guardian created before this existed.
    bulk_email_unsubscribe_token = models.CharField(max_length=64, unique=True, default=_generate_unsubscribe_token)
    bulk_email_unsubscribed_at = models.DateTimeField(null=True, blank=True)

    @property
    def full_name(self):
        return f"{self.first_name} {self.surname}".strip()

    def __str__(self):
        return self.full_name


class Campus(models.Model):
    """A physical TCS campus. Currently Main and Annex — Annex accepts only a
    subset of grades (see ANNEX_ACCEPTED_GRADES in serializers.py, checked by
    name against this row, so renaming a Campus row in admin silently
    disables that check — small, known tradeoff for a 2-row lookup table)."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "campuses"

    def __str__(self):
        return self.name


class Student(models.Model):
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
    ]

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="students")
    full_name = models.CharField(max_length=255)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, default="")
    nationality = models.CharField(max_length=100, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    town_city = models.CharField(max_length=255, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    current_school = models.CharField(
        max_length=255, blank=True, default="", help_text="e.g. 'N/A' if not yet enrolled anywhere"
    )
    previous_school_location = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Address/location of current_school — distinct from current_school itself, which is the school's name",
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
    campus = models.ForeignKey(
        Campus, on_delete=models.PROTECT, null=True, blank=True, related_name="applications",
    )

    wants_scholarship_info = models.BooleanField(default=False)
    scholarship_interest_details = models.TextField(blank=True, default="")

    # Declaration — typed-name + checkbox, same trust level as everything else
    # in this system (see Offer's docstring). declaration_agreed covers the
    # indemnity/data-protection/accuracy declaration and is required to
    # submit; media_consent_agreed is a separate, independently-revocable
    # opt-in per the consent text's own language, so it's a distinct field
    # rather than folded into declaration_agreed.
    declaration_signature_name = models.CharField(max_length=255, blank=True, default="")
    declaration_agreed = models.BooleanField(default=False)
    declaration_agreed_at = models.DateTimeField(null=True, blank=True)
    # Best-effort audit trail, not a verified identity — request.META['REMOTE_ADDR']
    # may reflect a proxy hop (Cloudflare/Cloud Run) rather than the parent's
    # real IP unless X-Forwarded-For parsing is added later.
    declaration_ip_address = models.GenericIPAddressField(null=True, blank=True)
    media_consent_agreed = models.BooleanField(default=False)

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
    token link (the /offer page). No parent portal exists yet, so the
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
    """Seats available per (academic_year, year_group, campus). Campus-scoped
    since TCS's two campuses are physically separate seat pools — a null
    campus means "not campus-specific" (e.g. a grade only ever offered at one
    campus). Note: Postgres treats NULL as never equal to itself, so
    unique_together doesn't stop two campus=NULL rows for the same
    (year, grade) — acceptable for this table's scale (hand-managed by
    admin staff), not worth a partial unique index.

    ApplicationAdmin shows a soft warning (never a hard block — real
    admissions has legitimate reasons to go over on paper: sibling priority,
    board exceptions) when saving a Decision as "accepted" would put the
    accepted count for that (year, grade, campus) over this capacity. See
    ApplicationAdmin._warn_if_over_capacity in admin.py."""

    academic_year = models.CharField(max_length=50)
    year_group = models.CharField(max_length=50)
    campus = models.ForeignKey(
        Campus, on_delete=models.PROTECT, null=True, blank=True, related_name="capacities",
        help_text="Leave blank if this grade's capacity isn't campus-specific.",
    )
    capacity = models.PositiveIntegerField()

    class Meta:
        unique_together = ("academic_year", "year_group", "campus")
        verbose_name_plural = "capacities"

    def __str__(self):
        campus_label = f" @ {self.campus}" if self.campus else ""
        return f"{self.year_group} {self.academic_year}{campus_label}: {self.capacity} seats"


class Document(models.Model):
    TYPE_CHOICES = [
        ("proof_of_vaccination", "Proof of Vaccination"),
        ("financial_clearance", "Proof of Financial Clearance from Previous School"),
        ("previous_report", "Report Card / Transcript from Previous School"),
        ("application_fee_proof", "Proof of Application Fee Payment"),
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


class EmergencyContact(models.Model):
    """Who to call in an emergency for this specific Application's student —
    Application-scoped (not Student- or Family-scoped) so it shows up as an
    inline on ApplicationAdmin alongside Decision/Offer/Document/Note, the
    same page staff already use for everything else on a submission."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="emergency_contacts")
    name = models.CharField(max_length=255)
    relationship = models.CharField(max_length=100, help_text="Free text — e.g. aunt, family friend, grandmother")
    phone = models.CharField(max_length=32)

    def __str__(self):
        return f"{self.name} ({self.relationship}) — {self.application}"


class HealthInfo(models.Model):
    """Real health data about a child — access restricted via the
    admissions.can_view_health_info permission (see HealthInfoInline in
    admin.py), not visible to every staff member who can view an Application
    by default. Never surfaced in list_display, search_fields, or exports."""

    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name="health_info")

    has_learning_or_physical_needs = models.BooleanField(default=False)
    learning_or_physical_needs_details = models.TextField(blank=True, default="")

    has_medical_conditions = models.BooleanField(default=False)
    medical_conditions_details = models.TextField(blank=True, default="")

    has_allergies_or_dietary_requirements = models.BooleanField(default=False)
    allergies_dietary_details = models.TextField(blank=True, default="")

    class Meta:
        permissions = [("can_view_health_info", "Can view health/wellbeing info")]

    def __str__(self):
        return f"Health info — {self.application}"


class ApplicationDraft(models.Model):
    """A parent's in-progress Application, saved before there's a real
    Student/Guardian/Application row to attach it to — the multi-step public
    form can be genuinely incomplete/invalid at any point (no email yet, a
    malformed date), so raw form state is kept as JSON rather than partial
    real rows. Full validation only ever happens once, at final submit, via
    the real ApplicationSerializer — one source of truth for what "valid"
    means, not a second looser one for drafts.

    The token is the sole access control, same trust model as Offer's resume
    link — no parent login system exists. This is a deliberate stopgap, not
    a replacement for one; see docs/admissions/01-vision.md."""

    token = models.CharField(max_length=64, unique=True, default=_generate_draft_token)
    email = models.EmailField(blank=True, default="", help_text="Captured once the guardian email step is filled in")
    data = models.JSONField(default=dict, blank=True)
    current_step = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(default=_default_draft_expiry)
    submitted_application = models.ForeignKey(
        Application, null=True, blank=True, on_delete=models.SET_NULL, related_name="draft",
    )

    def __str__(self):
        return f"Draft ({self.email or 'no email yet'}) — step {self.current_step}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    @property
    def is_submitted(self):
        return self.submitted_application_id is not None


class Note(models.Model):
    """Internal, staff-only notes on an application. Never shown to parents."""

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Note on {self.application} ({self.created_at:%Y-%m-%d})"


class EmailCampaign(models.Model):
    """Phase 6 — a bulk/marketing send to Guardians (never used for
    transactional email — those go through emails.py directly, unaffected by
    anything here). Recipients are computed once, when staff hit Send (see
    admin.py's send_campaign action / bulk_email.py's compute_recipients) —
    not recalculated afterward, so this stays an accurate historical record
    of who a real campaign actually went to even if Guardian data changes
    later. filter_* fields are optional narrowing on top of "every Guardian
    not currently unsubscribed" (opt-out by default, not an explicit list)."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("queued", "Queued"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    name = models.CharField(max_length=255, help_text="Internal label — not shown to recipients")
    subject = models.CharField(max_length=255, help_text="Supports {{placeholders}} — see bulk_email.py")
    body = models.TextField(
        help_text="Plain text. Supports {{guardian_first_name}}, {{guardian_full_name}}, "
        "{{student_names}}, {{unsubscribe_link}} — {{unsubscribe_link}} is required.",
    )
    filter_stage = models.CharField(
        max_length=20, choices=Application.STAGE_CHOICES, blank=True, default="",
        help_text="Blank = no filter (every stage)",
    )
    filter_academic_year = models.CharField(max_length=50, blank=True, default="", help_text="Blank = no filter")
    filter_campus = models.ForeignKey(
        Campus, null=True, blank=True, on_delete=models.PROTECT, help_text="Blank = no filter",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    total_recipients = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(
        default=0, help_text="Recipients skipped before ever reaching Resend — malformed or placeholder addresses.",
    )

    class Meta:
        permissions = [("can_send_bulk_email", "Can send bulk/marketing email campaigns")]

    def clean(self):
        if "{{unsubscribe_link}}" not in self.body:
            raise ValidationError({"body": "The body must include {{unsubscribe_link}} — required for every bulk send."})

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"


class EmailCampaignRecipient(models.Model):
    """One row per Guardian a campaign was (or will be) sent to — the audit
    trail. email is a snapshot of Guardian.email at send time, since a
    Guardian's address could change after the fact and this should reflect
    what was actually used. No bounced_at/opened_at here by design (see
    docs/admissions/02-stack-and-schema.md) — that needs Resend webhooks and
    is deliberately out of scope for this first cut. resend_message_id is
    kept anyway (free, from the batch API's own response) so a future
    webhook-based bounce feature can correlate back to this row without a
    schema change."""

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("skipped_unsubscribed", "Skipped (unsubscribed)"),
        ("skipped_invalid", "Skipped (invalid address)"),
    ]

    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name="recipients")
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="bulk_email_recipient_records")
    email = models.EmailField()
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default="pending")
    resend_message_id = models.CharField(max_length=100, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("campaign", "guardian")

    def __str__(self):
        return f"{self.email} — {self.campaign.name} ({self.get_status_display()})"
