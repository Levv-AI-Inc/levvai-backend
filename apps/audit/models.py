from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=128)
    object_id = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.action} {self.object_type} {self.object_id}"
