from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class IntakeRequest(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    ENGAGEMENT_STAFFING = "staffing"
    ENGAGEMENT_SOW = "sow"
    ENGAGEMENT_OTHER = "other"

    ENGAGEMENT_CHOICES = [
        (ENGAGEMENT_STAFFING, "Staffing"),
        (ENGAGEMENT_SOW, "SOW"),
        (ENGAGEMENT_OTHER, "Other"),
    ]

    RATE_HOURLY = "hourly"
    RATE_DAILY = "daily"

    RATE_UNIT_CHOICES = [
        (RATE_HOURLY, "Hourly"),
        (RATE_DAILY, "Daily"),
    ]

    # Optional denormalized tenant identifier for analytics/export.
    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    engagement_type = models.CharField(max_length=16, choices=ENGAGEMENT_CHOICES, default=ENGAGEMENT_STAFFING)
    approval_status = models.CharField(
        max_length=16,
        choices=[
            ("not_started", "Not Started"),
            ("processing", "Processing"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="not_started",
        db_index=True,
    )

    cost_center = models.ForeignKey(
        "masterdata.CostCenter",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests",
    )
    site = models.ForeignKey(
        "masterdata.Site",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests",
    )
    supplier = models.ForeignKey(
        "masterdata.Supplier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests",
    )
    role_definition = models.ForeignKey(
        "masterdata.RoleDefinition",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests",
    )
    legal_entity = models.ForeignKey(
        "masterdata.LegalEntity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests",
    )

    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    worker_count = models.PositiveIntegerField(null=True, blank=True)
    target_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_unit = models.CharField(max_length=16, choices=RATE_UNIT_CHOICES, default=RATE_HOURLY)
    budget_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    country = models.CharField(max_length=2, blank=True)
    state_province = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    rate_card = models.ForeignKey(
        "rates.RateCard",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests",
    )
    overtime_enabled = models.BooleanField(default=False)
    overtime_multiplier = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)
    qualifications_enabled = models.BooleanField(default=False, db_index=True)
    approval_chain = models.ForeignKey(
        "approvals.ApprovalChain",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests",
    )
    approval_chain_snapshot = models.JSONField(default=dict, blank=True)
    approval_started_at = models.DateTimeField(null=True, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests_submitted",
    )
    decision_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_requests_decided",
    )
    decision_reason = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        return f"IntakeRequest<{self.id}> {self.status}"


class IntakeSnapshot(models.Model):
    intake = models.ForeignKey(IntakeRequest, on_delete=models.CASCADE, related_name="snapshots")
    version = models.PositiveIntegerField(default=1)
    snapshot_json = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_snapshots_created",
    )

    class Meta:
        unique_together = ("intake", "version")
        ordering = ["-version"]

    def __str__(self):
        return f"IntakeSnapshot<{self.intake_id}> v{self.version}"


class IntakeQualification(models.Model):
    TYPE_SKILL = "skill"
    TYPE_CERTIFICATION = "certification"
    TYPE_EDUCATION = "education"
    TYPE_LANGUAGE = "language"
    TYPE_TOOL = "tool"
    TYPE_OTHER = "other"

    TYPE_CHOICES = [
        (TYPE_SKILL, "Skill"),
        (TYPE_CERTIFICATION, "Certification"),
        (TYPE_EDUCATION, "Education"),
        (TYPE_LANGUAGE, "Language"),
        (TYPE_TOOL, "Tool"),
        (TYPE_OTHER, "Other"),
    ]

    GROUP_MUST_HAVE = "must_have"
    GROUP_NICE_TO_HAVE = "nice_to_have"

    GROUP_CHOICES = [
        (GROUP_MUST_HAVE, "Must Have"),
        (GROUP_NICE_TO_HAVE, "Nice To Have"),
    ]

    RESPONSE_YEARS = "years"
    RESPONSE_RATING = "rating"
    RESPONSE_YES_NO = "yes_no"
    RESPONSE_TEXT = "text"

    RESPONSE_CHOICES = [
        (RESPONSE_YEARS, "Years of Experience"),
        (RESPONSE_RATING, "Rating"),
        (RESPONSE_YES_NO, "Yes / No"),
        (RESPONSE_TEXT, "Text"),
    ]

    PROFICIENCY_BEGINNER = "Beginner"
    PROFICIENCY_INTERMEDIATE = "Intermediate"
    PROFICIENCY_ADVANCED = "Advanced"
    PROFICIENCY_EXPERT = "Expert"

    PROFICIENCY_CHOICES = [
        (PROFICIENCY_BEGINNER, "Beginner"),
        (PROFICIENCY_INTERMEDIATE, "Intermediate"),
        (PROFICIENCY_ADVANCED, "Advanced"),
        (PROFICIENCY_EXPERT, "Expert"),
    ]

    intake = models.ForeignKey(
        IntakeRequest,
        on_delete=models.CASCADE,
        related_name="qualifications",
    )
    sequence = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=255)
    qualification_type = models.CharField(max_length=32, choices=TYPE_CHOICES, default=TYPE_SKILL)
    group = models.CharField(max_length=32, choices=GROUP_CHOICES, default=GROUP_MUST_HAVE)
    description = models.TextField(blank=True)
    mandatory = models.BooleanField(default=False)
    knockout = models.BooleanField(default=False)
    response_mode = models.CharField(max_length=16, choices=RESPONSE_CHOICES, default=RESPONSE_YEARS)
    min_years = models.PositiveIntegerField(default=0)
    proficiency = models.CharField(max_length=16, choices=PROFICIENCY_CHOICES, default=PROFICIENCY_INTERMEDIATE)
    weight = models.PositiveSmallIntegerField(default=1)
    tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["intake", "sequence"],
                name="intake_qualification_unique_sequence",
            ),
            models.CheckConstraint(
                check=models.Q(sequence__gte=1),
                name="intake_qualification_sequence_gte_1",
            ),
            models.CheckConstraint(
                check=models.Q(weight__gte=1) & models.Q(weight__lte=5),
                name="intake_qualification_weight_1_5",
            ),
        ]
        indexes = [
            models.Index(fields=["intake", "group"]),
            models.Index(fields=["qualification_type", "response_mode"]),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.description = (self.description or "").strip()

        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if self.weight < 1 or self.weight > 5:
            raise ValidationError({"weight": "Weight must be between 1 and 5."})

        if self.tags is None:
            self.tags = []
        if not isinstance(self.tags, list):
            raise ValidationError({"tags": "Tags must be a list."})

        normalized_tags = []
        for tag in self.tags:
            text = str(tag).strip()
            if text:
                normalized_tags.append(text)
        self.tags = normalized_tags

    def __str__(self):
        return f"{self.intake_id} #{self.sequence} {self.name}"


class IntakeSelectedCandidate(models.Model):
    STATUS_SUBMITTED = "submitted"
    STATUS_REVIEWED = "reviewed"
    STATUS_ACCEPTED = "accepted"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_REVIEWED, "Reviewed"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REJECTED, "Rejected"),
    ]

    intake = models.ForeignKey(
        IntakeRequest,
        on_delete=models.CASCADE,
        related_name="selected_candidates",
    )
    supplier = models.ForeignKey(
        "masterdata.Supplier",
        on_delete=models.CASCADE,
        related_name="intake_selected_candidates",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="intake_selected_candidates_submitted",
    )
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    resume_url = models.URLField(blank=True)
    available_start_date = models.DateField(null=True, blank=True)
    proposed_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SUBMITTED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["intake", "status"]),
            models.Index(fields=["supplier", "status"]),
            models.Index(fields=["submitted_by", "created_at"]),
        ]

    def clean(self):
        self.full_name = (self.full_name or "").strip()
        self.phone = (self.phone or "").strip()
        self.notes = (self.notes or "").strip()
        self.currency = (self.currency or "").strip().upper()

        if not self.full_name:
            raise ValidationError({"full_name": "This field may not be blank."})
        if self.proposed_rate is not None and self.proposed_rate < 0:
            raise ValidationError({"proposed_rate": "Proposed rate must be greater than or equal to 0."})
        if self.currency and len(self.currency) != 3:
            raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

    def __str__(self):
        return f"IntakeSelectedCandidate<{self.intake_id}:{self.id}> {self.full_name}"
