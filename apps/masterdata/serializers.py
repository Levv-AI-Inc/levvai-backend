from rest_framework import serializers

from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    CustomField,
    JobTemplate,
    RateCard,
    Site,
    Supplier,
)


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name"]


class BusinessUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessUnit
        fields = ["id", "name", "company"]


class CostCenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostCenter
        fields = ["id", "code", "name", "business_unit"]


class SiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Site
        fields = ["id", "name", "address"]


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "email"]


class RateCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = RateCard
        fields = ["id", "name", "rate_type", "amount", "currency"]


class CustomFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomField
        fields = ["id", "name", "schema"]


class JobTemplateSerializer(serializers.ModelSerializer):
    region = serializers.CharField(source="region_in_country", read_only=True)

    class Meta:
        model = JobTemplate
        fields = [
            "id",
            "role",
            "description",
            "country",
            "region_in_country",
            "region",
            "created_at",
            "updated_at",
        ]


class JobTemplateUploadItemSerializer(serializers.Serializer):
    role = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=64)
    region_in_country = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    region = serializers.CharField(max_length=255, required=False, allow_blank=True, write_only=True)

    def validate(self, attrs):
        role = (attrs.get("role") or "").strip()
        country = (attrs.get("country") or "").strip().upper()
        description = (attrs.get("description") or "").strip()
        region = attrs.pop("region", None)
        region_in_country = attrs.get("region_in_country") or ""
        if not region_in_country and region is not None:
            region_in_country = region
        region_in_country = region_in_country.strip()

        if not role:
            raise serializers.ValidationError({"role": "This field may not be blank."})
        if not country:
            raise serializers.ValidationError({"country": "This field may not be blank."})

        attrs["role"] = role
        attrs["country"] = country
        attrs["description"] = description
        attrs["region_in_country"] = region_in_country
        return attrs
