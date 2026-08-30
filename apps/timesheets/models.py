from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Timesheet(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_VOIDED = "voided"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_VOIDED, "Voided"),
    ]

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="timesheets",
    )
    worker_profile = models.ForeignKey(
        "accounts.WorkerProfile",
        on_delete=models.CASCADE,
        related_name="timesheets",
    )
    worker_engagement = models.ForeignKey(
        "accounts.WorkerEngagement",
        on_delete=models.PROTECT,
        related_name="timesheets",
    )
    engagement_type = models.CharField(max_length=32)
    work_order_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    work_order_number = models.CharField(max_length=64, blank=True)
    sow_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    sow_number = models.CharField(max_length=64, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    total_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    regular_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, blank=True)
    bill_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    pay_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    comment = models.TextField(blank=True)
    anomaly_reason = models.TextField(blank=True)
    qa_issues = models.JSONField(default=list, blank=True)
    jurisdiction_flags = models.JSONField(default=list, blank=True)
    approval_brief = models.JSONField(default=dict, blank=True)
    assignment_snapshot = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timesheets_submitted",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timesheets_approved",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timesheets_rejected",
    )
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_start", "-id"]
        indexes = [
            models.Index(fields=["tenant", "status", "period_start"]),
            models.Index(fields=["worker_profile", "status"]),
            models.Index(fields=["worker_engagement", "period_start"]),
            models.Index(fields=["tenant", "work_order_id", "period_start"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(total_hours__gte=0),
                name="timesheet_total_hours_gte_0",
            ),
            models.CheckConstraint(
                check=Q(regular_hours__gte=0),
                name="timesheet_regular_hours_gte_0",
            ),
            models.CheckConstraint(
                check=Q(overtime_hours__gte=0),
                name="timesheet_overtime_hours_gte_0",
            ),
            models.CheckConstraint(
                check=Q(period_end__gte=models.F("period_start")),
                name="timesheet_period_end_gte_start",
            ),
            models.UniqueConstraint(
                fields=["worker_engagement", "period_start", "period_end"],
                condition=~Q(status="voided"),
                name="timesheet_unique_active_engagement_period",
            ),
        ]

    def clean(self):
        self.work_order_number = (self.work_order_number or "").strip()
        self.sow_number = (self.sow_number or "").strip()
        self.currency = (self.currency or "").strip().upper()
        self.comment = (self.comment or "").strip()
        self.anomaly_reason = (self.anomaly_reason or "").strip()
        self.rejection_reason = (self.rejection_reason or "").strip()

        if self.currency and len(self.currency) != 3:
            raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end cannot be earlier than period start."})
        if self.worker_engagement_id:
            engagement = self.worker_engagement
            if self.worker_profile_id and engagement.worker_profile_id != self.worker_profile_id:
                raise ValidationError({"worker_engagement": "Engagement does not belong to this worker profile."})
            if self.tenant_id and engagement.tenant_id != self.tenant_id:
                raise ValidationError({"worker_engagement": "Engagement does not belong to this tenant."})

        if self.qa_issues is None:
            self.qa_issues = []
        if not isinstance(self.qa_issues, list):
            raise ValidationError({"qa_issues": "QA issues must be a list."})
        if self.jurisdiction_flags is None:
            self.jurisdiction_flags = []
        if not isinstance(self.jurisdiction_flags, list):
            raise ValidationError({"jurisdiction_flags": "Jurisdiction flags must be a list."})
        if self.approval_brief is None:
            self.approval_brief = {}
        if not isinstance(self.approval_brief, dict):
            raise ValidationError({"approval_brief": "Approval brief must be an object."})
        if self.assignment_snapshot is None:
            self.assignment_snapshot = {}
        if not isinstance(self.assignment_snapshot, dict):
            raise ValidationError({"assignment_snapshot": "Assignment snapshot must be an object."})

    def __str__(self):
        return f"Timesheet<{self.worker_engagement_id}:{self.period_start}:{self.status}>"


class TimesheetLine(models.Model):
    timesheet = models.ForeignKey(
        Timesheet,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_date = models.DateField()
    task_name = models.CharField(max_length=255)
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    cost_center_id = models.PositiveBigIntegerField(null=True, blank=True)
    cost_center_code = models.CharField(max_length=200, blank=True)
    cost_center_name = models.CharField(max_length=200, blank=True)
    task_code = models.CharField(max_length=100, blank=True)
    allocation_rationale = models.CharField(max_length=500, blank=True)
    rate_category = models.CharField(max_length=64, blank=True)
    bill_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    pay_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["line_date", "id"]
        indexes = [
            models.Index(fields=["timesheet", "line_date"]),
            models.Index(fields=["cost_center_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(hours__gte=0),
                name="timesheetline_hours_gte_0",
            ),
            models.CheckConstraint(
                check=Q(bill_amount__isnull=True) | Q(bill_amount__gte=0),
                name="timesheetline_bill_amount_gte_0",
            ),
            models.CheckConstraint(
                check=Q(pay_amount__isnull=True) | Q(pay_amount__gte=0),
                name="timesheetline_pay_amount_gte_0",
            ),
        ]

    def clean(self):
        self.task_name = (self.task_name or "").strip()
        self.cost_center_code = (self.cost_center_code or "").strip()
        self.cost_center_name = (self.cost_center_name or "").strip()
        self.task_code = (self.task_code or "").strip()
        self.allocation_rationale = (self.allocation_rationale or "").strip()
        self.rate_category = (self.rate_category or "").strip()
        if self.timesheet_id and self.line_date:
            timesheet = self.timesheet
            if not (timesheet.period_start <= self.line_date <= timesheet.period_end):
                raise ValidationError({"line_date": "Line date must fall within the timesheet period."})
        if not self.task_name:
            raise ValidationError({"task_name": "Task name is required."})

    def __str__(self):
        return f"TimesheetLine<{self.timesheet_id}:{self.line_date}:{self.hours}>"


class TimesheetEvent(models.Model):
    ACTION_CREATED = "created"
    ACTION_UPDATED = "updated"
    ACTION_SUBMITTED = "submitted"
    ACTION_APPROVED = "approved"
    ACTION_REJECTED = "rejected"
    ACTION_VOIDED = "voided"

    ACTION_CHOICES = [
        (ACTION_CREATED, "Created"),
        (ACTION_UPDATED, "Updated"),
        (ACTION_SUBMITTED, "Submitted"),
        (ACTION_APPROVED, "Approved"),
        (ACTION_REJECTED, "Rejected"),
        (ACTION_VOIDED, "Voided"),
    ]

    timesheet = models.ForeignKey(
        Timesheet,
        on_delete=models.CASCADE,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timesheet_events",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["timesheet", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def clean(self):
        self.note = (self.note or "").strip()
        if self.metadata is None:
            self.metadata = {}
        if not isinstance(self.metadata, dict):
            raise ValidationError({"metadata": "Metadata must be an object."})
