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
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)

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
