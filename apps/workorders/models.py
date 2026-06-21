from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class WorkOrder(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
    ]

    APPROVAL_NOT_STARTED = "not_started"
    APPROVAL_PROCESSING = "processing"
    APPROVAL_APPROVED = "approved"
    APPROVAL_REJECTED = "rejected"

    APPROVAL_STATUS_CHOICES = [
        (APPROVAL_NOT_STARTED, "Not Started"),
        (APPROVAL_PROCESSING, "Processing"),
        (APPROVAL_APPROVED, "Approved"),
        (APPROVAL_REJECTED, "Rejected"),
    ]

    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    work_order_number = models.CharField(max_length=64, null=True, blank=True, unique=True, db_index=True)

    intake = models.ForeignKey(
        "intake.IntakeRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    selected_candidate = models.ForeignKey(
        "intake.IntakeSelectedCandidate",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    supplier = models.ForeignKey(
        "masterdata.Supplier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    role_definition = models.ForeignKey(
        "masterdata.RoleDefinition",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    cost_center = models.ForeignKey(
        "masterdata.CostCenter",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    legal_entity = models.ForeignKey(
        "masterdata.LegalEntity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    site = models.ForeignKey(
        "masterdata.Site",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )
    approval_chain = models.ForeignKey(
        "approvals.ApprovalChain",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders",
    )

    worker_full_name = models.CharField(max_length=255, blank=True)
    worker_email = models.EmailField(blank=True)
    worker_phone = models.CharField(max_length=64, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    approval_status = models.CharField(
        max_length=16,
        choices=APPROVAL_STATUS_CHOICES,
        default=APPROVAL_NOT_STARTED,
        db_index=True,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    bill_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    pay_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    hours_per_week = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    overtime_enabled = models.BooleanField(default=False)
    overtime_multiplier = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    budget_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    work_location_label = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    resume_url = models.URLField(blank=True)
    approval_chain_snapshot = models.JSONField(default=dict, blank=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders_submitted",
    )
    decision_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders_decided",
    )
    decision_reason = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="work_orders_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant_id", "status", "created_at"]),
            models.Index(fields=["tenant_id", "approval_status", "created_at"]),
            models.Index(fields=["intake", "status"]),
            models.Index(fields=["supplier", "status"]),
            models.Index(fields=["created_by", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(bill_rate__isnull=True) | Q(bill_rate__gte=0),
                name="workorder_bill_rate_gte_0",
            ),
            models.CheckConstraint(
                check=Q(pay_rate__isnull=True) | Q(pay_rate__gte=0),
                name="workorder_pay_rate_gte_0",
            ),
            models.CheckConstraint(
                check=Q(hours_per_week__isnull=True) | Q(hours_per_week__gte=0),
                name="workorder_hours_per_week_gte_0",
            ),
            models.CheckConstraint(
                check=Q(overtime_multiplier__isnull=True) | Q(overtime_multiplier__gt=0),
                name="workorder_overtime_multiplier_gt_0",
            ),
            models.CheckConstraint(
                check=Q(estimated_cost__isnull=True) | Q(estimated_cost__gte=0),
                name="workorder_estimated_cost_gte_0",
            ),
            models.CheckConstraint(
                check=Q(budget_amount__isnull=True) | Q(budget_amount__gte=0),
                name="workorder_budget_amount_gte_0",
            ),
        ]

    def clean(self):
        self.work_order_number = (self.work_order_number or "").strip() or None
        self.worker_full_name = (self.worker_full_name or "").strip()
        self.worker_phone = (self.worker_phone or "").strip()
        self.currency = (self.currency or "").strip().upper()
        self.work_location_label = (self.work_location_label or "").strip()
        self.notes = (self.notes or "").strip()
        self.decision_reason = (self.decision_reason or "").strip()

        if self.currency and len(self.currency) != 3:
            raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be earlier than start date."})

        if self.overtime_enabled and self.overtime_multiplier is None:
            raise ValidationError(
                {"overtime_multiplier": "Overtime multiplier is required when overtime is enabled."}
            )

        if self.risk_flags is None:
            self.risk_flags = []
        if not isinstance(self.risk_flags, list):
            raise ValidationError({"risk_flags": "Risk flags must be a list of strings."})

        normalized_flags = []
        for item in self.risk_flags:
            text = str(item).strip()
            if text:
                normalized_flags.append(text)
        self.risk_flags = normalized_flags

    def __str__(self):
        return self.work_order_number or f"WorkOrder<{self.id}>"
