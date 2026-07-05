from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

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


class WorkerLifecycleWorkflow(models.Model):
    TYPE_ONBOARDING = "onboarding"
    TYPE_OFFBOARDING = "offboarding"

    TYPE_CHOICES = [
        (TYPE_ONBOARDING, "Onboarding"),
        (TYPE_OFFBOARDING, "Offboarding"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    workflow_type = models.CharField(max_length=24, choices=TYPE_CHOICES, default=TYPE_ONBOARDING, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    version = models.PositiveIntegerField(default=1)
    dependencies = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="worker_lifecycle_workflows_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["workflow_type", "name", "id"]
        indexes = [
            models.Index(fields=["tenant_id", "workflow_type", "status"]),
            models.Index(fields=["tenant_id", "is_active", "workflow_type"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(version__gte=1), name="worker_workflow_version_gte_1"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})
        if self.dependencies is None:
            self.dependencies = []
        if not isinstance(self.dependencies, list):
            raise ValidationError({"dependencies": "Dependencies must be a list."})

    def __str__(self):
        return f"{self.name} ({self.workflow_type})"


class WorkflowPolicyScope(models.Model):
    WORKER_TYPE_CONTINGENT = "contingent"
    WORKER_TYPE_EMPLOYEE = "employee"
    WORKER_TYPE_CONTRACTOR = "contractor"

    WORKER_TYPE_CHOICES = [
        (WORKER_TYPE_CONTINGENT, "Contingent"),
        (WORKER_TYPE_EMPLOYEE, "Employee"),
        (WORKER_TYPE_CONTRACTOR, "Contractor"),
    ]

    workflow = models.OneToOneField(
        WorkerLifecycleWorkflow,
        on_delete=models.CASCADE,
        related_name="policy_scope",
    )
    worker_type = models.CharField(max_length=32, choices=WORKER_TYPE_CHOICES, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        self.worker_type = (self.worker_type or "").strip().lower()

    def __str__(self):
        return f"Scope<{self.workflow_id}>"


class WorkflowPolicyScopeField(models.Model):
    FIELD_LOCATION = "location"
    FIELD_COST_CENTER = "cost_center"
    FIELD_BUSINESS_UNIT = "business_unit"
    FIELD_ROLE = "role"
    FIELD_SUPPLIER = "supplier"

    FIELD_CHOICES = [
        (FIELD_LOCATION, "Location"),
        (FIELD_COST_CENTER, "Cost Center"),
        (FIELD_BUSINESS_UNIT, "Business Unit"),
        (FIELD_ROLE, "Role"),
        (FIELD_SUPPLIER, "Supplier"),
    ]

    OPERATOR_EQUALS = "equals"

    OPERATOR_CHOICES = [
        (OPERATOR_EQUALS, "Equals"),
    ]

    scope = models.ForeignKey(
        WorkflowPolicyScope,
        on_delete=models.CASCADE,
        related_name="fields",
    )
    sequence = models.PositiveIntegerField(default=1)
    field_key = models.CharField(max_length=32, choices=FIELD_CHOICES)
    operator = models.CharField(max_length=16, choices=OPERATOR_CHOICES, default=OPERATOR_EQUALS)
    location = models.ForeignKey(
        "masterdata.Location",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="workflow_scope_fields",
    )
    cost_center = models.ForeignKey(
        "masterdata.CostCenter",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="workflow_scope_fields",
    )
    business_unit = models.ForeignKey(
        "masterdata.BusinessUnit",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="workflow_scope_fields",
    )
    role_definition = models.ForeignKey(
        "masterdata.RoleDefinition",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="workflow_scope_fields",
    )
    supplier = models.ForeignKey(
        "masterdata.Supplier",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="workflow_scope_fields",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(fields=["scope", "sequence"], name="workflow_scope_field_unique_sequence"),
            models.UniqueConstraint(fields=["scope", "field_key"], name="workflow_scope_field_unique_key"),
            models.CheckConstraint(check=Q(sequence__gte=1), name="workflow_scope_field_sequence_gte_1"),
        ]
        indexes = [
            models.Index(fields=["field_key"]),
        ]

    def clean(self):
        self.field_key = (self.field_key or "").strip().lower()
        self.operator = (self.operator or self.OPERATOR_EQUALS).strip().lower()
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})

        field_to_attr = {
            self.FIELD_LOCATION: "location",
            self.FIELD_COST_CENTER: "cost_center",
            self.FIELD_BUSINESS_UNIT: "business_unit",
            self.FIELD_ROLE: "role_definition",
            self.FIELD_SUPPLIER: "supplier",
        }
        expected_attr = field_to_attr.get(self.field_key)
        if not expected_attr:
            raise ValidationError({"field_key": "Unsupported scope field."})

        populated = [
            attr
            for attr in field_to_attr.values()
            if getattr(self, f"{attr}_id", None)
        ]
        if populated != [expected_attr]:
            raise ValidationError(
                {
                    expected_attr: (
                        f"{expected_attr} is required for {self.field_key} scope fields, "
                        "and no other scope target may be set."
                    )
                }
            )

    def target(self):
        for attr in ["location", "cost_center", "business_unit", "role_definition", "supplier"]:
            value = getattr(self, attr, None)
            if value is not None:
                return value
        return None

    def __str__(self):
        return f"{self.scope_id} / {self.field_key}"


class WorkflowRequirement(models.Model):
    OWNER_WORKER = "worker"
    OWNER_SUPPLIER = "supplier"
    OWNER_HIRING_MANAGER = "hiring_manager"
    OWNER_IT = "it"
    OWNER_SYSTEM = "system"

    OWNER_CHOICES = [
        (OWNER_WORKER, "Worker"),
        (OWNER_SUPPLIER, "Supplier"),
        (OWNER_HIRING_MANAGER, "Hiring Manager"),
        (OWNER_IT, "IT"),
        (OWNER_SYSTEM, "System"),
    ]

    tenant_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    code = models.CharField(max_length=128, unique=True, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    default_owner = models.CharField(max_length=32, choices=OWNER_CHOICES, default=OWNER_WORKER)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [
            models.Index(fields=["tenant_id", "is_active"]),
            models.Index(fields=["name"]),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.description = (self.description or "").strip()
        self.code = (self.code or "").strip()
        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})
        if not self.code:
            self.code = self._generate_code()

    def _generate_code(self):
        base = slugify(self.name).replace("-", "_")[:112] or "requirement"
        candidate = base
        suffix = 2
        while WorkflowRequirement.objects.filter(code=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base[:112-len(str(suffix))-1]}_{suffix}"
            suffix += 1
        return candidate

    def __str__(self):
        return self.name


class WorkflowBlock(models.Model):
    TYPE_REQUIREMENT = "requirement"
    TYPE_SYSTEM = "system"

    TYPE_CHOICES = [
        (TYPE_REQUIREMENT, "Requirement"),
        (TYPE_SYSTEM, "System"),
    ]

    GATE_HARD = "hard"
    GATE_SOFT = "soft"

    GATE_CHOICES = [
        (GATE_HARD, "Hard"),
        (GATE_SOFT, "Soft"),
    ]

    INTEGRATION_API_CALL = "api_call"

    INTEGRATION_CHOICES = [
        (INTEGRATION_API_CALL, "API Call"),
    ]

    workflow = models.ForeignKey(
        WorkerLifecycleWorkflow,
        on_delete=models.CASCADE,
        related_name="blocks",
    )
    sequence = models.PositiveIntegerField(default=1)
    block_type = models.CharField(max_length=24, choices=TYPE_CHOICES)
    name = models.CharField(max_length=255)
    gate_type = models.CharField(max_length=16, choices=GATE_CHOICES, default=GATE_HARD)
    integration_type = models.CharField(max_length=64, choices=INTEGRATION_CHOICES, blank=True)
    client_key = models.CharField(max_length=128, blank=True, db_index=True)
    config = models.JSONField(default=dict, blank=True)
    layout = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(fields=["workflow", "sequence"], name="workflow_block_unique_sequence"),
            models.UniqueConstraint(
                fields=["workflow", "client_key"],
                condition=Q(client_key__gt=""),
                name="workflow_block_unique_client_key",
            ),
            models.CheckConstraint(check=Q(sequence__gte=1), name="workflow_block_sequence_gte_1"),
        ]
        indexes = [
            models.Index(fields=["workflow", "block_type"]),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.block_type = (self.block_type or "").strip().lower()
        self.gate_type = (self.gate_type or self.GATE_HARD).strip().lower()
        self.integration_type = (self.integration_type or "").strip().lower()

        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if self.config is None:
            self.config = {}
        if not isinstance(self.config, dict):
            raise ValidationError({"config": "Config must be an object."})
        if self.layout is None:
            self.layout = {}
        if not isinstance(self.layout, dict):
            raise ValidationError({"layout": "Layout must be an object."})
        if self.block_type == self.TYPE_SYSTEM and not self.integration_type:
            raise ValidationError({"integration_type": "Integration type is required for system blocks."})
        if self.block_type == self.TYPE_REQUIREMENT:
            self.integration_type = ""

    def __str__(self):
        return f"{self.workflow_id} #{self.sequence} {self.name}"


class WorkflowBlockRequirement(models.Model):
    block = models.ForeignKey(
        WorkflowBlock,
        on_delete=models.CASCADE,
        related_name="requirements",
    )
    requirement = models.ForeignKey(
        WorkflowRequirement,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="block_instances",
    )
    sequence = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=255, blank=True)
    owner = models.CharField(
        max_length=32,
        choices=WorkflowRequirement.OWNER_CHOICES,
        default=WorkflowRequirement.OWNER_WORKER,
    )
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(fields=["block", "sequence"], name="workflow_block_req_unique_sequence"),
            models.CheckConstraint(check=Q(sequence__gte=1), name="workflow_block_req_sequence_gte_1"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.owner = (self.owner or WorkflowRequirement.OWNER_WORKER).strip().lower()
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if self.requirement and not self.name:
            self.name = self.requirement.name
        if not self.name:
            raise ValidationError({"name": "Requirement name is required."})
        if self.config is None:
            self.config = {}
        if not isinstance(self.config, dict):
            raise ValidationError({"config": "Config must be an object."})
        if self.block_id and self.block.block_type != WorkflowBlock.TYPE_REQUIREMENT:
            raise ValidationError({"block": "Requirements can only be attached to requirement blocks."})

    def __str__(self):
        return f"{self.block_id} #{self.sequence} {self.name}"
