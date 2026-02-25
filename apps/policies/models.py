from django.db import models

from apps.accounts.models import Membership


class FieldPolicy(models.Model):
    """Field-level access rules for a role.

    Used to mask or block read/write access to specific fields by role.
    Enforcement happens at the serializer layer (output filtering + input validation).
    """
    model = models.CharField(max_length=64)
    field_name = models.CharField(max_length=128)
    role = models.CharField(max_length=32, choices=Membership.ROLE_CHOICES)
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("role", "model", "field_name")
        indexes = [
            models.Index(fields=["model", "role"]),
        ]

    def __str__(self):
        return f"{self.model}.{self.field_name} ({self.role})"
