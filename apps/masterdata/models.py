from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.text import slugify
from zoneinfo import ZoneInfo


class Company(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class LegalEntity(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
    ]

    id = models.CharField(max_length=200, primary_key=True)
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=2)
    tax_id = models.CharField(max_length=50, blank=True)
    currency = models.CharField(max_length=3)
    erp_code = models.CharField(max_length=200, blank=True)
    billing_address = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.country:
            self.country = self.country.upper()
            if len(self.country) != 2:
                raise ValidationError({"country": "Country must be a 2-letter ISO 3166-1 alpha-2 code."})

        if self.currency:
            self.currency = self.currency.upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

    def __str__(self):
        return f"{self.id} - {self.name}"


class BusinessUnit(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
    ]

    code = models.CharField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "self",
        to_field="code",
        db_column="parent_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    description = models.CharField(max_length=500, blank=True)
    legal_entity_id = models.CharField(max_length=200, blank=True)
    gl_account_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Legacy link kept nullable for compatibility with existing company records.
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name="business_units")

    def clean(self):
        if self.parent_id and self.code and self.parent_id == self.code:
            raise ValidationError({"parent_id": "Business unit cannot be parent of itself."})

        if not self.parent_id:
            return

        depth = 1
        visited = {self.code} if self.code else set()
        node = self.parent

        while node:
            if node.code in visited:
                raise ValidationError({"parent_id": "Business unit hierarchy cannot contain cycles."})
            visited.add(node.code)
            depth += 1
            if depth > 5:
                raise ValidationError({"parent_id": "Business unit hierarchy supports up to 5 levels."})
            node = node.parent

    def __str__(self):
        return f"{self.code} - {self.name}"


class CostCenter(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
    ]

    PERIOD_ANNUAL = "annual"
    PERIOD_QUARTERLY = "quarterly"
    PERIOD_PROJECT = "project"

    PERIOD_CHOICES = [
        (PERIOD_ANNUAL, "Annual"),
        (PERIOD_QUARTERLY, "Quarterly"),
        (PERIOD_PROJECT, "Project"),
    ]

    code = models.CharField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner_email = models.EmailField(max_length=255)
    business_unit = models.ForeignKey(
        BusinessUnit,
        to_field="code",
        db_column="business_unit_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cost_centers",
    )
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    budget_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    budget_period = models.CharField(max_length=16, choices=PERIOD_CHOICES, null=True, blank=True)
    gl_account_id = models.CharField(max_length=100, blank=True)
    erp_code = models.CharField(max_length=200, blank=True)
    legal_entity_id = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(budget_amount__isnull=True) | Q(budget_amount__gte=0),
                name="costcenter_budget_non_negative",
            ),
        ]

    def clean(self):
        if self.currency:
            self.currency = self.currency.upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

        if self.owner_email:
            user_model = get_user_model()
            if not user_model.objects.filter(email__iexact=self.owner_email).exists():
                raise ValidationError({"owner_email": "Owner email must reference an existing user email."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class Location(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
    ]

    name = models.CharField(max_length=200)
    country = models.CharField(max_length=64)
    region = models.CharField(max_length=100)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "country", "region", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "country", "region"],
                name="location_unique_name_country_region",
            ),
        ]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["country", "region"]),
            models.Index(fields=["status"]),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.country = (self.country or "").strip()
        self.region = (self.region or "").strip()

        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})
        if not self.country:
            raise ValidationError({"country": "This field may not be blank."})
        if not self.region:
            raise ValidationError({"region": "This field may not be blank."})

    def __str__(self):
        return f"{self.name} - {self.country} ({self.region})"


class Site(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_CLOSED, "Closed"),
    ]

    code = models.CharField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    address_line1 = models.CharField(max_length=600)
    address_line2 = models.CharField(max_length=600, blank=True)
    city = models.CharField(max_length=100)
    state_province = models.CharField(max_length=100)
    country = models.CharField(max_length=2)
    postal_code = models.CharField(max_length=20)
    latitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    timezone = models.CharField(max_length=50)
    hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    hours_per_week = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    legal_entity = models.ForeignKey(
        LegalEntity,
        db_column="legal_entity_id",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sites",
    )
    tax_config = models.JSONField(default=dict, blank=True)
    erp_code = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.country:
            self.country = self.country.upper()
            if len(self.country) != 2:
                raise ValidationError({"country": "Country must be a 2-letter ISO 3166-1 alpha-2 code."})

        if self.currency:
            self.currency = self.currency.upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})

        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except Exception as exc:
                raise ValidationError({"timezone": "Timezone must be a valid IANA timezone."}) from exc

        if self.hours_per_day is not None and not (1 <= self.hours_per_day <= 24):
            raise ValidationError({"hours_per_day": "Hours per day must be between 1 and 24."})

        if self.hours_per_week is not None and not (1 <= self.hours_per_week <= 168):
            raise ValidationError({"hours_per_week": "Hours per week must be between 1 and 168."})

        if self.latitude is not None and not (-90 <= self.latitude <= 90):
            raise ValidationError({"latitude": "Latitude must be between -90 and 90."})

        if self.longitude is not None and not (-180 <= self.longitude <= 180):
            raise ValidationError({"longitude": "Longitude must be between -180 and 180."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class Supplier(models.Model):
    TYPE_STAFFING = "staffing"
    TYPE_SERVICES = "services"
    TYPE_BOTH = "both"

    TYPE_CHOICES = [
        (TYPE_STAFFING, "Staffing"),
        (TYPE_SERVICES, "Services"),
        (TYPE_BOTH, "Both"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_INACTIVE = "inactive"
    STATUS_INVITED = "invited"

    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_INACTIVE, "Inactive"),
        (STATUS_INVITED, "Invited"),
    ]

    RISK_LOW = "low"
    RISK_MEDIUM = "medium"
    RISK_HIGH = "high"

    RISK_CHOICES = [
        (RISK_LOW, "Low"),
        (RISK_MEDIUM, "Medium"),
        (RISK_HIGH, "High"),
    ]

    COMPLIANCE_COMPLIANT = "compliant"
    COMPLIANCE_REVIEW_REQUIRED = "review_required"
    COMPLIANCE_NON_COMPLIANT = "non_compliant"

    COMPLIANCE_CHOICES = [
        (COMPLIANCE_COMPLIANT, "Compliant"),
        (COMPLIANCE_REVIEW_REQUIRED, "Review Required"),
        (COMPLIANCE_NON_COMPLIANT, "Non-Compliant"),
    ]

    supplier_code = models.CharField(max_length=64, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    contact_name = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=64, blank=True)
    tax_id = models.CharField(max_length=128, blank=True)
    diversity_status = models.CharField(max_length=128, blank=True)
    supplier_type = models.CharField(max_length=16, choices=TYPE_CHOICES, default=TYPE_STAFFING)
    category = models.CharField(max_length=255, blank=True)
    owner_name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    risk_level = models.CharField(max_length=16, choices=RISK_CHOICES, default=RISK_LOW)
    compliance_status = models.CharField(
        max_length=32,
        choices=COMPLIANCE_CHOICES,
        default=COMPLIANCE_COMPLIANT,
    )
    active_workers = models.PositiveIntegerField(default=0)
    active_sows = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class RateCard(models.Model):
    RATE_HOURLY = "hourly"
    RATE_DAILY = "daily"

    RATE_CHOICES = [
        (RATE_HOURLY, "Hourly"),
        (RATE_DAILY, "Daily"),
    ]

    name = models.CharField(max_length=255)
    rate_type = models.CharField(max_length=16, choices=RATE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    def __str__(self):
        return f"{self.name} ({self.rate_type})"


class CustomField(models.Model):
    name = models.CharField(max_length=255)
    schema = models.JSONField(default=dict)

    def __str__(self):
        return self.name


class JobTemplate(models.Model):
    role = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    country = models.CharField(max_length=64)
    region_in_country = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("role", "country", "region_in_country")
        ordering = ["role", "country", "region_in_country"]
        indexes = [
            models.Index(fields=["role"]),
            models.Index(fields=["country", "region_in_country"]),
        ]

    def __str__(self):
        region = f", {self.region_in_country}" if self.region_in_country else ""
        return f"{self.role} ({self.country}{region})"


class RoleDefinition(models.Model):
    UNIT_HOUR = "hour"
    UNIT_DAY = "day"

    UNIT_CHOICES = [
        (UNIT_HOUR, "Hour"),
        (UNIT_DAY, "Day"),
    ]

    code = models.CharField(max_length=255, unique=True, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    country = models.CharField(max_length=2)
    region = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    default_currency = models.CharField(max_length=3, default="USD")
    default_unit = models.CharField(max_length=16, choices=UNIT_CHOICES, default=UNIT_HOUR)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "country", "region", "city", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "country", "region", "city"],
                name="roledefinition_unique_market",
            ),
        ]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["country", "region", "city"]),
        ]

    def clean(self):
        self.name = (self.name or "").strip()
        self.description = (self.description or "").strip()
        self.country = (self.country or "").strip().upper()
        self.region = (self.region or "").strip()
        self.city = (self.city or "").strip()
        self.default_currency = (self.default_currency or "").strip().upper()

        if not self.name:
            raise ValidationError({"name": "This field may not be blank."})

        if len(self.country) != 2:
            raise ValidationError({"country": "Country must be a 2-letter ISO 3166-1 alpha-2 code."})

        if len(self.default_currency) != 3:
            raise ValidationError({"default_currency": "Currency must be a 3-letter ISO 4217 code."})

        if not self.code:
            self.code = self._generate_code()

    def _generate_code(self):
        parts = [self.name, self.country, self.region, self.city]
        base = slugify("-".join(part for part in parts if part))[:240] or "role"
        candidate = base
        suffix = 2
        while RoleDefinition.objects.filter(code=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base[:240-len(str(suffix))-1]}-{suffix}"
            suffix += 1
        return candidate

    @property
    def location_label(self):
        parts = [self.city, self.region, self.country]
        return ", ".join(part for part in parts if part)

    def __str__(self):
        location = f" - {self.location_label}" if self.location_label else ""
        return f"{self.name}{location}"
