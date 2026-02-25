from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


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
    ROLE_HIRING_MANAGER = "hiring_manager"
    ROLE_PROCUREMENT_MANAGER = "procurement_manager"
    ROLE_FINANCE = "finance"
    ROLE_LEGAL = "legal"
    ROLE_BUSINESS = "business"
    ROLE_EXECUTIVE = "executive"
    ROLE_SUPPLIER = "supplier"
    ROLE_MANAGER = "manager"
    ROLE_VIEWER = "viewer"

    ROLE_CHOICES = [
        (ROLE_ADMIN, "Admin"),
        (ROLE_HIRING_MANAGER, "Hiring Manager"),
        (ROLE_PROCUREMENT_MANAGER, "Procurement Manager"),
        (ROLE_FINANCE, "Finance"),
        (ROLE_LEGAL, "Legal"),
        (ROLE_BUSINESS, "Business"),
        (ROLE_EXECUTIVE, "Executive"),
        (ROLE_SUPPLIER, "Supplier User"),
        (ROLE_MANAGER, "Manager"),
        (ROLE_VIEWER, "Viewer"),
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
            models.Index(fields=["user", "tenant", "created_at"]),
        ]


class LoginAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    failed_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "tenant")
