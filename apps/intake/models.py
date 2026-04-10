from django.conf import settings
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

    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    worker_count = models.PositiveIntegerField(null=True, blank=True)
    target_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_unit = models.CharField(max_length=16, choices=RATE_UNIT_CHOICES, default=RATE_HOURLY)
    budget_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    custom_fields = models.JSONField(default=dict, blank=True)

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
