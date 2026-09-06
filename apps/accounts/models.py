import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    AUTH_PASSWORD = "password"
    AUTH_SSO = "sso"

    AUTH_CHOICES = [
        (AUTH_PASSWORD, "Password"),
        (AUTH_SSO, "SSO"),
    ]

    auth_type = models.CharField(max_length=16, choices=AUTH_CHOICES, default=AUTH_PASSWORD)


class Membership(models.Model):
    ROLE_ADMIN = "admin"
    ROLE_PROGRAM_MANAGER = "manager"
    ROLE_BUSINESS = "business"
    ROLE_SUPPLIER = "supplier"
    ROLE_FINANCE = "finance"
    ROLE_READ_ONLY = "viewer"

    # Legacy aliases kept for code compatibility while roles are consolidated.
    ROLE_MANAGER = ROLE_PROGRAM_MANAGER
    ROLE_VIEWER = ROLE_READ_ONLY
    ROLE_HIRING_MANAGER = ROLE_BUSINESS
    ROLE_PROCUREMENT_MANAGER = ROLE_PROGRAM_MANAGER
    ROLE_LEGAL = ROLE_READ_ONLY
    ROLE_EXECUTIVE = ROLE_READ_ONLY

    ROLE_CHOICES = [
        (ROLE_ADMIN, "System Admin"),
        (ROLE_PROGRAM_MANAGER, "Program Manager (PMO)"),
        (ROLE_BUSINESS, "Business User"),
        (ROLE_SUPPLIER, "Supplier User"),
        (ROLE_FINANCE, "Finance User"),
        (ROLE_READ_ONLY, "Read Only"),
    ]

    STATUS_INVITED = "invited"
    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"

    STATUS_CHOICES = [
        (STATUS_INVITED, "Invited"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_DISABLED, "Disabled"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    is_active = models.BooleanField(default=True)
    business_unit_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    cost_center_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    supplier_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "tenant")
        constraints = [
            models.CheckConstraint(
                check=Q(role="supplier", supplier_id__isnull=False) | ~Q(role="supplier"),
                name="membership_supplier_requires_supplier_id",
            )
        ]


    def clean(self):
        if self.tenant_id:
            from django_tenants.utils import schema_context
            from apps.masterdata.models import BusinessUnit, CostCenter

            with schema_context(self.tenant.schema_name):
                business_unit = None
                if self.business_unit_id:
                    business_unit = BusinessUnit.objects.filter(id=self.business_unit_id).first()
                    if not business_unit:
                        raise ValidationError({"business_unit_id": "Business unit does not exist for this tenant."})

                if self.cost_center_id:
                    cost_center = CostCenter.objects.filter(id=self.cost_center_id).first()
                    if not cost_center:
                        raise ValidationError({"cost_center_id": "Cost center does not exist for this tenant."})
                    if business_unit and cost_center.business_unit_id != business_unit.code:
                        raise ValidationError(
                            {"cost_center_id": "Cost center does not belong to the selected business unit."}
                        )

        if self.role == self.ROLE_SUPPLIER:
            if not self.supplier_id:
                raise ValidationError({"supplier_id": "Supplier users must be linked to a supplier."})
            if self.tenant_id:
                from django_tenants.utils import schema_context
                from apps.masterdata.models import Supplier

                with schema_context(self.tenant.schema_name):
                    if not Supplier.objects.filter(id=self.supplier_id).exists():
                        raise ValidationError({"supplier_id": "Supplier does not exist for this tenant."})
        else:
            if self.supplier_id:
                raise ValidationError({"supplier_id": "Only supplier users can set supplier_id."})

    def __str__(self):
        return f"{self.user_id} -> {self.tenant_id} ({self.role})"


class TenantSSOConfig(models.Model):
    tenant = models.OneToOneField("tenants.Tenant", on_delete=models.CASCADE, related_name="sso_config")
    workos_organization_id = models.CharField(max_length=255)
    workos_connection_id = models.CharField(max_length=255, blank=True, null=True)
    enabled = models.BooleanField(default=True)
    default_role = models.CharField(max_length=32, choices=Membership.ROLE_CHOICES, default=Membership.ROLE_BUSINESS)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.default_role == Membership.ROLE_SUPPLIER:
            raise ValidationError({"default_role": "Supplier role cannot be used for SSO users."})

    def __str__(self):
        return f"{self.tenant_id} -> WorkOS"


class WorkerProfile(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_DISABLED, "Disabled"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="worker_profile",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    preferred_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        self.preferred_name = (self.preferred_name or "").strip()
        self.phone = (self.phone or "").strip()

    def __str__(self):
        return f"WorkerProfile<{self.user_id}>"


class WorkerEngagement(models.Model):
    TYPE_WORK_ORDER = "work_order"
    TYPE_SOW = "sow"

    TYPE_CHOICES = [
        (TYPE_WORK_ORDER, "Work Order"),
        (TYPE_SOW, "SOW"),
    ]

    STATUS_INVITED = "invited"
    STATUS_ACTIVE = "active"
    STATUS_ENDED = "ended"
    STATUS_DISABLED = "disabled"

    STATUS_CHOICES = [
        (STATUS_INVITED, "Invited"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ENDED, "Ended"),
        (STATUS_DISABLED, "Disabled"),
    ]

    worker_profile = models.ForeignKey(
        WorkerProfile,
        on_delete=models.CASCADE,
        related_name="engagements",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="worker_engagements",
    )
    engagement_type = models.CharField(max_length=32, choices=TYPE_CHOICES, default=TYPE_WORK_ORDER)
    work_order_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    work_order_number = models.CharField(max_length=64, blank=True)
    sow_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    sow_number = models.CharField(max_length=64, blank=True)
    supplier_id = models.PositiveBigIntegerField(null=True, blank=True)
    supplier_name = models.CharField(max_length=255, blank=True)
    client_name = models.CharField(max_length=255, blank=True)
    role_name = models.CharField(max_length=255, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_INVITED, db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="worker_engagements_invited",
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["worker_profile", "status"]),
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "engagement_type", "work_order_id"]),
            models.Index(fields=["tenant", "engagement_type", "sow_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(engagement_type="work_order")
                    & (Q(work_order_id__isnull=False) | ~Q(work_order_number=""))
                )
                | (
                    Q(engagement_type="sow")
                    & (Q(sow_id__isnull=False) | ~Q(sow_number=""))
                ),
                name="worker_engagement_requires_reference",
            ),
        ]

    def clean(self):
        self.work_order_number = (self.work_order_number or "").strip()
        self.sow_number = (self.sow_number or "").strip()
        self.supplier_name = (self.supplier_name or "").strip()
        self.client_name = (self.client_name or "").strip()
        self.role_name = (self.role_name or "").strip()

        if self.engagement_type == self.TYPE_WORK_ORDER:
            if not self.work_order_id and not self.work_order_number:
                raise ValidationError({"work_order_id": "Work order engagement requires a work order reference."})
        elif self.engagement_type == self.TYPE_SOW:
            if not self.sow_id and not self.sow_number:
                raise ValidationError({"sow_id": "SOW engagement requires a SOW reference."})

        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be earlier than start date."})

    def __str__(self):
        reference = self.work_order_number or self.sow_number or self.work_order_id or self.sow_id
        return f"WorkerEngagement<{self.worker_profile_id}:{self.tenant_id}:{reference}>"


def _default_worker_invite_token():
    return f"worker_{secrets.token_urlsafe(32)}"


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

    worker_profile = models.ForeignKey(
        WorkerProfile,
        on_delete=models.CASCADE,
        related_name="invites",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="worker_invites",
    )
    # Work orders are tenant-schema models, so shared account records retain the
    # tenant-scoped identifier instead of a cross-schema foreign key.
    work_order_id = models.PositiveBigIntegerField(db_index=True)
    email = models.EmailField()
    token = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        default=_default_worker_invite_token,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
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
    expires_at = models.DateTimeField(default=_default_worker_invite_expiry, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["worker_profile", "email", "status"],
                name="acc_winvite_profile_email_idx",
            ),
            models.Index(
                fields=["tenant", "work_order_id", "status"],
                name="acc_winvite_tenant_wo_idx",
            ),
        ]

    def clean(self):
        self.email = (self.email or "").strip().lower()
        if self.worker_profile_id and self.email != (self.worker_profile.user.email or "").strip().lower():
            raise ValidationError({"email": "Invite email must match the worker email."})

    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def is_usable(self):
        return self.status == self.STATUS_PENDING and not self.is_expired()

    def mark_accepted(self, user=None):
        self.status = self.STATUS_ACCEPTED
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])

    def mark_expired(self):
        self.status = self.STATUS_EXPIRED
        self.save(update_fields=["status", "updated_at"])


class PasswordPolicy(models.Model):
    min_length = models.PositiveSmallIntegerField(default=12)
    min_character_sets = models.PositiveSmallIntegerField(default=3)
    history_count = models.PositiveSmallIntegerField(default=5)
    max_failed_attempts = models.PositiveSmallIntegerField(default=5)
    lockout_minutes = models.PositiveSmallIntegerField(default=15)
    block_common_passwords = models.BooleanField(default=True)
    expiration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "PasswordPolicy"


class PasswordHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    password_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "tenant", "created_at"],
                name="accounts_pa_user_te_1a0b6f_idx",
            ),
        ]


class LoginAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    failed_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "tenant")


def _default_supplier_invite_token():
    return secrets.token_urlsafe(32)


def _default_supplier_invite_expiry():
    return timezone.now() + timedelta(days=7)


class SupplierInvite(models.Model):
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

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, related_name="supplier_invites")
    supplier_id = models.PositiveBigIntegerField()
    email = models.EmailField()
    token = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        default=_default_supplier_invite_token,
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supplier_invites_sent",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="supplier_invites_accepted",
    )
    expires_at = models.DateTimeField(default=_default_supplier_invite_expiry, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "email", "status"]),
            models.Index(fields=["tenant", "supplier_id", "status"]),
        ]

    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    def is_usable(self):
        return self.status == self.STATUS_PENDING and not self.is_expired()

    def mark_accepted(self, user=None):
        self.status = self.STATUS_ACCEPTED
        self.accepted_at = timezone.now()
        self.accepted_by = user
        self.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])

    def mark_expired(self):
        self.status = self.STATUS_EXPIRED
        self.save(update_fields=["status", "updated_at"])
