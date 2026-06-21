from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.text import slugify


class RateStructure(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    CURRENCY_MODE_SINGLE = "single_currency"

    CURRENCY_MODE_CHOICES = [
        (CURRENCY_MODE_SINGLE, "Single Currency"),
    ]

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    currency_mode = models.CharField(
        max_length=32,
        choices=CURRENCY_MODE_CHOICES,
        default=CURRENCY_MODE_SINGLE,
    )
    rounding_scale = models.PositiveSmallIntegerField(default=2)
    is_default = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [
            models.Index(fields=["status", "name"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(rounding_scale__gte=0), name="rates_structure_rounding_gte_0"),
            models.CheckConstraint(check=Q(rounding_scale__lte=6), name="rates_structure_rounding_lte_6"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.description = (self.description or "").strip()
        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})
        if self.rounding_scale < 0 or self.rounding_scale > 6:
            raise ValidationError({"rounding_scale": "Rounding scale must be between 0 and 6."})

    def __str__(self):
        return self.name


class RateStructureComponent(models.Model):
    VALUE_CURRENCY = "currency"
    VALUE_PERCENTAGE = "percentage"

    VALUE_TYPE_CHOICES = [
        (VALUE_CURRENCY, "Currency"),
        (VALUE_PERCENTAGE, "Percentage"),
    ]

    ROLE_BASE = "base"
    ROLE_ADDITIVE_PERCENT = "additive_percent"
    ROLE_ADDITIVE_AMOUNT = "additive_amount"

    CALCULATION_ROLE_CHOICES = [
        (ROLE_BASE, "Base"),
        (ROLE_ADDITIVE_PERCENT, "Additive Percent"),
        (ROLE_ADDITIVE_AMOUNT, "Additive Amount"),
    ]

    rate_structure = models.ForeignKey(
        RateStructure,
        on_delete=models.CASCADE,
        related_name="components",
    )
    sequence = models.PositiveIntegerField(default=1)
    code = models.CharField(max_length=64, blank=True)
    label = models.CharField(max_length=255)
    value_type = models.CharField(max_length=16, choices=VALUE_TYPE_CHOICES)
    calculation_role = models.CharField(max_length=32, choices=CALCULATION_ROLE_CHOICES)
    is_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["rate_structure", "sequence"],
                name="rates_component_unique_sequence",
            ),
            models.UniqueConstraint(
                fields=["rate_structure", "code"],
                name="rates_component_unique_code",
            ),
            models.UniqueConstraint(
                fields=["rate_structure"],
                condition=Q(calculation_role="base"),
                name="rates_component_single_base",
            ),
            models.CheckConstraint(check=Q(sequence__gte=1), name="rates_component_sequence_gte_1"),
            models.CheckConstraint(
                check=Q(calculation_role="base", value_type="currency") | ~Q(calculation_role="base"),
                name="rates_component_base_requires_currency",
            ),
            models.CheckConstraint(
                check=Q(calculation_role="additive_percent", value_type="percentage")
                | ~Q(calculation_role="additive_percent"),
                name="rates_component_percent_requires_percentage",
            ),
            models.CheckConstraint(
                check=Q(calculation_role="additive_amount", value_type="currency")
                | ~Q(calculation_role="additive_amount"),
                name="rates_component_amount_requires_currency",
            ),
        ]
        indexes = [
            models.Index(fields=["rate_structure", "is_active"]),
        ]

    def clean(self):
        self.label = (self.label or "").strip()
        self.code = (self.code or "").strip()

        if not self.label:
            raise ValidationError({"label": "This field may not be blank."})
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})

        if not self.code:
            self.code = self._generate_code()

    def _generate_code(self):
        base = slugify(self.label).replace("-", "_")[:58] or "component"
        candidate = base
        suffix = 2
        queryset = RateStructureComponent.objects.all()
        if self.rate_structure_id:
            queryset = queryset.filter(rate_structure_id=self.rate_structure_id)
        while queryset.filter(code=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base[:58-len(str(suffix))-1]}_{suffix}"
            suffix += 1
        return candidate

    def __str__(self):
        return f"{self.rate_structure} / {self.label}"


class RateCard(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    UNIT_HOUR = "hour"
    UNIT_DAY = "day"

    UNIT_CHOICES = [
        (UNIT_HOUR, "Hour"),
        (UNIT_DAY, "Day"),
    ]

    name = models.CharField(max_length=255)
    role_definition = models.ForeignKey(
        "masterdata.RoleDefinition",
        on_delete=models.PROTECT,
        related_name="rate_cards",
    )
    currency = models.CharField(max_length=3)
    unit = models.CharField(max_length=16, choices=UNIT_CHOICES)
    effective_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    rate_structure = models.ForeignKey(
        RateStructure,
        on_delete=models.PROTECT,
        related_name="rate_cards",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-effective_date", "name", "id"]
        indexes = [
            models.Index(fields=["status", "effective_date"]),
            models.Index(fields=["role_definition", "status", "effective_date"]),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.notes = (self.notes or "").strip()
        self.currency = (self.currency or "").strip().upper()

        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})

        if len(self.currency) != 3:
            raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

        if self.end_date and self.effective_date and self.end_date < self.effective_date:
            raise ValidationError({"end_date": "End date cannot be earlier than effective date."})

    def __str__(self):
        return self.name


class RateCardLine(models.Model):
    rate_card = models.ForeignKey(
        RateCard,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    sequence = models.PositiveIntegerField(default=1)
    supplier = models.ForeignKey(
        "masterdata.Supplier",
        on_delete=models.PROTECT,
        related_name="rate_card_lines",
    )
    location_label = models.CharField(max_length=255, blank=True)
    bill_rate = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["rate_card", "sequence"],
                name="rates_card_line_unique_sequence",
            ),
            models.UniqueConstraint(
                fields=["rate_card", "supplier", "location_label"],
                name="rates_card_line_unique_supplier_location",
            ),
            models.CheckConstraint(check=Q(sequence__gte=1), name="rates_card_line_sequence_gte_1"),
            models.CheckConstraint(check=Q(bill_rate__gte=0), name="rates_card_line_bill_rate_gte_0"),
        ]
        indexes = [
            models.Index(fields=["rate_card", "supplier"]),
        ]

    def clean(self):
        self.location_label = (self.location_label or "").strip()
        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if self.bill_rate is not None and self.bill_rate < 0:
            raise ValidationError({"bill_rate": "Bill rate must be greater than or equal to 0."})

    def __str__(self):
        return f"{self.rate_card} / {self.supplier}"


class RateCardLineValue(models.Model):
    rate_card_line = models.ForeignKey(
        RateCardLine,
        on_delete=models.CASCADE,
        related_name="component_values",
    )
    rate_structure_component = models.ForeignKey(
        RateStructureComponent,
        on_delete=models.PROTECT,
        related_name="line_values",
    )
    numeric_value = models.DecimalField(max_digits=14, decimal_places=4)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rate_card_line", "rate_structure_component"],
                name="rates_line_value_unique_component",
            ),
        ]
        indexes = [
            models.Index(fields=["rate_card_line", "rate_structure_component"]),
        ]

    def clean(self):
        if self.rate_card_line_id and self.rate_structure_component_id:
            card_structure_id = self.rate_card_line.rate_card.rate_structure_id
            component_structure_id = self.rate_structure_component.rate_structure_id
            if card_structure_id != component_structure_id:
                raise ValidationError(
                    {"rate_structure_component": "Component must belong to the same rate structure as the rate card."}
                )

    def __str__(self):
        return f"{self.rate_card_line} / {self.rate_structure_component}"


class RateRule(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    ACTION_MULTIPLY_BILL_RATE = "multiply_bill_rate"
    ACTION_ADD_PERCENT = "add_percent"
    ACTION_ADD_AMOUNT = "add_amount"

    ACTION_CHOICES = [
        (ACTION_MULTIPLY_BILL_RATE, "Multiply Bill Rate"),
        (ACTION_ADD_PERCENT, "Add Percent"),
        (ACTION_ADD_AMOUNT, "Add Amount"),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    priority = models.PositiveIntegerField(default=100, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    rate_structure = models.ForeignKey(
        RateStructure,
        on_delete=models.PROTECT,
        related_name="rate_rules",
        null=True,
        blank=True,
    )
    role_definition = models.ForeignKey(
        "masterdata.RoleDefinition",
        on_delete=models.PROTECT,
        related_name="rate_rules",
        null=True,
        blank=True,
    )
    effective_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    action_type = models.CharField(max_length=32, choices=ACTION_CHOICES, default=ACTION_MULTIPLY_BILL_RATE)
    action_value = models.DecimalField(max_digits=10, decimal_places=4)
    stop_processing = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name", "id"]
        indexes = [
            models.Index(fields=["status", "priority", "effective_date"]),
            models.Index(fields=["role_definition", "status", "effective_date"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(priority__gte=1), name="rates_rule_priority_gte_1"),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.description = (self.description or "").strip()

        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})

        if self.priority < 1:
            raise ValidationError({"priority": "Priority must be greater than or equal to 1."})

        if self.action_value is None or self.action_value <= 0:
            raise ValidationError({"action_value": "Action value must be greater than 0."})

        if self.end_date and self.effective_date and self.end_date < self.effective_date:
            raise ValidationError({"end_date": "End date cannot be earlier than effective date."})

    def __str__(self):
        return self.name


class RateRuleCondition(models.Model):
    JOIN_AND = "and"
    JOIN_OR = "or"

    JOINER_CHOICES = [
        (JOIN_AND, "AND"),
        (JOIN_OR, "OR"),
    ]

    rate_rule = models.ForeignKey(
        RateRule,
        on_delete=models.CASCADE,
        related_name="conditions",
    )
    sequence = models.PositiveIntegerField(default=1)
    joiner = models.CharField(max_length=8, choices=JOINER_CHOICES, default=JOIN_AND)
    field_key = models.CharField(max_length=64)
    operator = models.CharField(max_length=16)
    value_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["rate_rule", "sequence"],
                name="rates_rule_condition_unique_sequence",
            ),
            models.CheckConstraint(check=Q(sequence__gte=1), name="rates_rule_condition_sequence_gte_1"),
        ]
        indexes = [
            models.Index(fields=["field_key", "operator"]),
        ]

    def clean(self):
        self.field_key = (self.field_key or "").strip()
        self.operator = (self.operator or "").strip()

        if self.sequence < 1:
            raise ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if not self.field_key:
            raise ValidationError({"field_key": "This field may not be blank."})
        if not self.operator:
            raise ValidationError({"operator": "This field may not be blank."})

    def __str__(self):
        return f"{self.rate_rule} / {self.field_key} {self.operator}"
