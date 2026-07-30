import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from apps.policies.models import (
    WorkerLifecycleWorkflow,
    WorkflowBlock,
    WorkflowRequirement,
)


class Engagement(models.Model):
    STATUS_PENDING_SUPPLIER_ACCEPTANCE = "pending_supplier_acceptance"
    STATUS_ACCEPTED = "accepted"
    STATUS_CHANGES_REQUESTED = "changes_requested"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING_SUPPLIER_ACCEPTANCE, "Pending Supplier Acceptance"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_CHANGES_REQUESTED, "Changes Requested"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    engagement_number = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    work_order = models.OneToOneField(
        "workorders.WorkOrder",
        on_delete=models.PROTECT,
        related_name="engagement",
    )
    status = models.CharField(
        max_length=40,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING_SUPPLIER_ACCEPTANCE,
        db_index=True,
    )
    supplier_response_notes = models.TextField(blank=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="engagements_accepted",
    )
    change_requested_at = models.DateTimeField(null=True, blank=True)
    change_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="engagement_changes_requested",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="engagements_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant_id", "status", "created_at"]),
        ]

    def clean(self):
        self.engagement_number = (self.engagement_number or "").strip() or None
        self.supplier_response_notes = (self.supplier_response_notes or "").strip()
        if self.source_snapshot is None:
            self.source_snapshot = {}
        if not isinstance(self.source_snapshot, dict):
            raise ValidationError({"source_snapshot": "Source snapshot must be an object."})

    def __str__(self):
        return self.engagement_number or f"Engagement<{self.id}>"


class Worker(models.Model):
    STATUS_INVITED = "invited"
    STATUS_ONBOARDING = "onboarding"
    STATUS_ACTIVE = "active"
    STATUS_OFFBOARDING = "offboarding"
    STATUS_OFFBOARDED = "offboarded"

    STATUS_CHOICES = [
        (STATUS_INVITED, "Invited"),
        (STATUS_ONBOARDING, "Onboarding"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_OFFBOARDING, "Offboarding"),
        (STATUS_OFFBOARDED, "Offboarded"),
    ]

    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="worker_profiles",
    )
    email = models.EmailField(db_index=True)
    full_name = models.CharField(max_length=255)
    phone = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_INVITED,
        db_index=True,
    )
    registered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name", "id"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                models.F("tenant_id"),
                name="worker_tenant_email_case_insensitive_unique",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(user__isnull=False),
                name="worker_user_unique_when_set",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant_id", "status"]),
        ]

    def clean(self):
        self.email = (self.email or "").strip().lower()
        self.full_name = (self.full_name or "").strip()
        self.phone = (self.phone or "").strip()
        if not self.email:
            raise ValidationError({"email": "Worker email is required."})
        if not self.full_name:
            raise ValidationError({"full_name": "Worker name is required."})

    def __str__(self):
        return f"{self.full_name} <{self.email}>"


class WorkerEngagement(models.Model):
    """A worker's assignment to a work order.

    The model name and optional engagement relation are retained for
    compatibility with assignments created before work-order-first onboarding.
    """

    WORKER_TYPE_CONTINGENT = "contingent"
    WORKER_TYPE_EMPLOYEE = "employee"
    WORKER_TYPE_CONTRACTOR = "contractor"

    WORKER_TYPE_CHOICES = [
        (WORKER_TYPE_CONTINGENT, "Contingent"),
        (WORKER_TYPE_EMPLOYEE, "Employee"),
        (WORKER_TYPE_CONTRACTOR, "Contractor"),
    ]

    STATUS_ONBOARDING = "onboarding"
    STATUS_ACTIVE = "active"
    STATUS_OFFBOARDING = "offboarding"
    STATUS_COMPLETE = "complete"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_ONBOARDING, "Onboarding"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_OFFBOARDING, "Offboarding"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    worker = models.ForeignKey(
        Worker,
        on_delete=models.PROTECT,
        related_name="engagements",
    )
    engagement = models.OneToOneField(
        Engagement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="worker_engagement",
    )
    work_order = models.OneToOneField(
        "workorders.WorkOrder",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="worker_assignment",
    )
    worker_type = models.CharField(
        max_length=32,
        choices=WORKER_TYPE_CHOICES,
        default=WORKER_TYPE_CONTINGENT,
        db_index=True,
    )
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_ONBOARDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant_id", "status"]),
            models.Index(fields=["worker", "status"]),
        ]

    def __str__(self):
        return f"{self.worker_id} / WO-{self.work_order_id or 'legacy'}"

    @property
    def resolved_work_order(self):
        if self.work_order_id:
            return self.work_order
        if self.engagement_id:
            return self.engagement.work_order
        return None


class LifecycleRun(models.Model):
    TYPE_ONBOARDING = WorkerLifecycleWorkflow.TYPE_ONBOARDING
    TYPE_OFFBOARDING = WorkerLifecycleWorkflow.TYPE_OFFBOARDING
    TYPE_CHOICES = WorkerLifecycleWorkflow.TYPE_CHOICES

    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_BLOCKED = "blocked"
    STATUS_COMPLETE = "complete"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    worker_engagement = models.ForeignKey(
        WorkerEngagement,
        on_delete=models.PROTECT,
        related_name="lifecycle_runs",
    )
    lifecycle_type = models.CharField(
        max_length=24,
        choices=TYPE_CHOICES,
        db_index=True,
    )
    workflow = models.ForeignKey(
        WorkerLifecycleWorkflow,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lifecycle_runs",
    )
    workflow_name = models.CharField(max_length=255)
    workflow_version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    snapshot = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["worker_engagement", "lifecycle_type"],
                name="worker_engagement_lifecycle_type_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant_id", "lifecycle_type", "status"]),
        ]

    def clean(self):
        self.workflow_name = (self.workflow_name or "").strip()
        if not self.workflow_name:
            raise ValidationError({"workflow_name": "Workflow name is required."})
        if self.snapshot is None:
            self.snapshot = {}
        if not isinstance(self.snapshot, dict):
            raise ValidationError({"snapshot": "Snapshot must be an object."})

    def __str__(self):
        return f"{self.worker_engagement_id} / {self.lifecycle_type}"


class LifecycleBlock(models.Model):
    STATUS_GATED = "gated"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_BLOCKED = "blocked"
    STATUS_COMPLETE = "complete"
    STATUS_SKIPPED = "skipped"

    STATUS_CHOICES = [
        (STATUS_GATED, "Gated"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    run = models.ForeignKey(
        LifecycleRun,
        on_delete=models.CASCADE,
        related_name="blocks",
    )
    source_block_id = models.PositiveBigIntegerField(null=True, blank=True)
    client_key = models.CharField(max_length=128, blank=True, db_index=True)
    sequence = models.PositiveIntegerField(default=1)
    block_type = models.CharField(max_length=24, choices=WorkflowBlock.TYPE_CHOICES)
    name = models.CharField(max_length=255)
    gate_type = models.CharField(
        max_length=16,
        choices=WorkflowBlock.GATE_CHOICES,
        default=WorkflowBlock.GATE_HARD,
    )
    integration_type = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_GATED,
        db_index=True,
    )
    config = models.JSONField(default=dict, blank=True)
    layout = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "sequence"],
                name="lifecycle_block_unique_sequence",
            ),
            models.UniqueConstraint(
                fields=["run", "client_key"],
                condition=Q(client_key__gt=""),
                name="lifecycle_block_unique_client_key",
            ),
        ]

    def __str__(self):
        return f"{self.run_id} #{self.sequence} {self.name}"


class LifecycleActivity(models.Model):
    STATUS_PENDING = "pending"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_BLOCKED = "blocked"
    STATUS_COMPLETE = "complete"
    STATUS_WAIVED = "waived"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_WAIVED, "Waived"),
    ]

    block = models.ForeignKey(
        LifecycleBlock,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    source_requirement_id = models.PositiveBigIntegerField(null=True, blank=True)
    sequence = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=255)
    owner = models.CharField(
        max_length=32,
        choices=WorkflowRequirement.OWNER_CHOICES,
        default=WorkflowRequirement.OWNER_WORKER,
        db_index=True,
    )
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    config = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lifecycle_activities_completed",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["block", "sequence"],
                name="lifecycle_activity_unique_sequence",
            ),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.notes = (self.notes or "").strip()
        if not self.name:
            raise ValidationError({"name": "Activity name is required."})
        if self.config is None:
            self.config = {}
        if not isinstance(self.config, dict):
            raise ValidationError({"config": "Config must be an object."})
        if self.evidence is None:
            self.evidence = {}
        if not isinstance(self.evidence, dict):
            raise ValidationError({"evidence": "Evidence must be an object."})

    def __str__(self):
        return f"{self.block_id} #{self.sequence} {self.name}"


def _default_worker_invite_token():
    return secrets.token_urlsafe(32)


def _default_worker_invite_expiry():
    return timezone.now() + timedelta(days=7)


class WorkerInvite(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_EXPIRED, "Expired"),
    ]

    worker = models.ForeignKey(
        Worker,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    engagement = models.ForeignKey(
        Engagement,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="worker_invites",
    )
    work_order = models.ForeignKey(
        "workorders.WorkOrder",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="worker_invites",
    )
    email = models.EmailField()
    token = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        default=_default_worker_invite_token,
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="worker_invites_sent",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="worker_invites_accepted",
    )
    expires_at = models.DateTimeField(
        default=_default_worker_invite_expiry,
        db_index=True,
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["worker", "email", "status"]),
        ]

    def clean(self):
        self.email = (self.email or "").strip().lower()
        if self.worker_id and self.email != self.worker.email:
            raise ValidationError({"email": "Invite email must match the worker email."})

    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def is_usable(self):
        return self.status == self.STATUS_PENDING and not self.is_expired()

    def mark_accepted(self, user=None):
        self.status = self.STATUS_ACCEPTED
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save(
            update_fields=[
                "status",
                "accepted_at",
                "accepted_by",
                "updated_at",
            ]
        )

    def mark_expired(self):
        self.status = self.STATUS_EXPIRED
        self.save(update_fields=["status", "updated_at"])

    def __str__(self):
        return f"{self.worker_id} / {self.email} / {self.status}"
