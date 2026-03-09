from django.db import models


class Company(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name


class BusinessUnit(models.Model):
    name = models.CharField(max_length=255)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="business_units")

    def __str__(self):
        return self.name


class CostCenter(models.Model):
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    business_unit = models.ForeignKey(BusinessUnit, on_delete=models.CASCADE, related_name="cost_centers")

    def __str__(self):
        return f"{self.code} - {self.name}"


class Site(models.Model):
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)

    def __str__(self):
        return self.name


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
