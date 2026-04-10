from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class ApprovalChain(models.Model):
    MATCH_ALL = "all"
    MATCH_ANY = "any"

    MATCH_STRATEGY_CHOICES = [
        (MATCH_ALL, "All Conditions"),
        (MATCH_ANY, "Any Condition"),
    ]

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    priority = models.PositiveIntegerField(default=50, db_index=True)
    match_strategy = models.CharField(max_length=8, choices=MATCH_STRATEGY_CHOICES, default=MATCH_ALL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name", "id"]
        indexes = [
            models.Index(fields=["is_active", "priority"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(priority__gte=1), name="approval_chain_priority_gte_1"),
        ]

    def clean(self):
        if self.priority < 1:
            raise ValidationError({"priority": "Priority must be greater than or equal to 1."})

    def __str__(self):
        return f"{self.name} (priority={self.priority})"


class ApprovalChainCondition(models.Model):
    approval_chain = models.ForeignKey(ApprovalChain, on_delete=models.CASCADE, related_name="conditions")
    sequence = models.PositiveIntegerField(default=1)
    field_key = models.CharField(max_length=128)
    operator = models.CharField(max_length=32)
    value_json = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["sequence", "id"]
        unique_together = ("approval_chain", "sequence")
        indexes = [
            models.Index(fields=["approval_chain", "sequence"]),
            models.Index(fields=["field_key", "operator"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(sequence__gte=1), name="approval_condition_sequence_gte_1"),
        ]

    def clean(self):
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})

    def __str__(self):
        return f"{self.approval_chain_id}:{self.sequence} {self.field_key} {self.operator}"


class ApprovalChainStep(models.Model):
    TYPE_SPECIFIC_USER = "specific_user"

    STEP_TYPE_CHOICES = [
        (TYPE_SPECIFIC_USER, "Specific User"),
    ]

    approval_chain = models.ForeignKey(ApprovalChain, on_delete=models.CASCADE, related_name="steps")
    sequence = models.PositiveIntegerField(default=1)
    step_type = models.CharField(max_length=32, choices=STEP_TYPE_CHOICES, default=TYPE_SPECIFIC_USER)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approval_chain_steps",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    class Meta:
        ordering = ["sequence", "id"]
        unique_together = ("approval_chain", "sequence")
        indexes = [
            models.Index(fields=["approval_chain", "sequence"]),
            models.Index(fields=["approver"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(sequence__gte=1), name="approval_step_sequence_gte_1"),
            models.CheckConstraint(check=Q(amount__gte=0), name="approval_step_amount_gte_0"),
        ]

    def clean(self):
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if self.amount is not None and self.amount < 0:
            raise ValidationError({"amount": "Amount must be greater than or equal to 0."})
        if self.currency:
            self.currency = self.currency.upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

    def __str__(self):
        return f"{self.approval_chain_id}:{self.sequence} -> {self.approver_id}"

