from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    CustomField,
    JobTemplate,
    LegalEntity,
    RateCard,
    Site,
    Supplier,
)


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "name"]


class LegalEntitySerializer(serializers.ModelSerializer):
    class Meta:
        model = LegalEntity
        fields = [
            "id",
            "name",
            "country",
            "tax_id",
            "currency",
            "erp_code",
            "billing_address",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        legal_entity = LegalEntity(
            pk=instance.pk if instance else attrs.get("id"),
            id=attrs.get("id", getattr(instance, "id", None)),
            name=attrs.get("name", getattr(instance, "name", None)),
            country=attrs.get("country", getattr(instance, "country", "")),
            tax_id=attrs.get("tax_id", getattr(instance, "tax_id", "")),
            currency=attrs.get("currency", getattr(instance, "currency", "")),
            erp_code=attrs.get("erp_code", getattr(instance, "erp_code", "")),
            billing_address=attrs.get("billing_address", getattr(instance, "billing_address", {})),
            status=attrs.get("status", getattr(instance, "status", LegalEntity.STATUS_ACTIVE)),
        )
        try:
            legal_entity.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["country"] = legal_entity.country
        attrs["currency"] = legal_entity.currency
        return attrs


class BusinessUnitSerializer(serializers.ModelSerializer):
    parent = serializers.SlugRelatedField(
        slug_field="code",
        queryset=BusinessUnit.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = BusinessUnit
        fields = [
            "id",
            "code",
            "name",
            "parent",
            "description",
            "legal_entity_id",
            "gl_account_id",
            "status",
            "company",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        business_unit = BusinessUnit(
            pk=instance.pk if instance else None,
            code=attrs.get("code", getattr(instance, "code", None)),
            name=attrs.get("name", getattr(instance, "name", None)),
            parent=attrs.get("parent", getattr(instance, "parent", None)),
            description=attrs.get("description", getattr(instance, "description", "")),
            legal_entity_id=attrs.get("legal_entity_id", getattr(instance, "legal_entity_id", "")),
            gl_account_id=attrs.get("gl_account_id", getattr(instance, "gl_account_id", "")),
            status=attrs.get("status", getattr(instance, "status", BusinessUnit.STATUS_ACTIVE)),
            company=attrs.get("company", getattr(instance, "company", None)),
        )
        try:
            business_unit.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})
        return attrs


class CostCenterSerializer(serializers.ModelSerializer):
    business_unit = serializers.SlugRelatedField(
        slug_field="code",
        queryset=BusinessUnit.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = CostCenter
        fields = [
            "id",
            "code",
            "name",
            "description",
            "owner_email",
            "business_unit",
            "currency",
            "status",
            "budget_amount",
            "budget_period",
            "gl_account_id",
            "erp_code",
            "legal_entity_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        cost_center = CostCenter(
            pk=instance.pk if instance else None,
            code=attrs.get("code", getattr(instance, "code", None)),
            name=attrs.get("name", getattr(instance, "name", None)),
            description=attrs.get("description", getattr(instance, "description", "")),
            owner_email=attrs.get("owner_email", getattr(instance, "owner_email", "")),
            business_unit=attrs.get("business_unit", getattr(instance, "business_unit", None)),
            currency=attrs.get("currency", getattr(instance, "currency", "USD")),
            status=attrs.get("status", getattr(instance, "status", CostCenter.STATUS_ACTIVE)),
            budget_amount=attrs.get("budget_amount", getattr(instance, "budget_amount", None)),
            budget_period=attrs.get("budget_period", getattr(instance, "budget_period", None)),
            gl_account_id=attrs.get("gl_account_id", getattr(instance, "gl_account_id", "")),
            erp_code=attrs.get("erp_code", getattr(instance, "erp_code", "")),
            legal_entity_id=attrs.get("legal_entity_id", getattr(instance, "legal_entity_id", "")),
        )
        try:
            cost_center.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["currency"] = cost_center.currency
        return attrs


class SiteSerializer(serializers.ModelSerializer):
    legal_entity = serializers.PrimaryKeyRelatedField(
        queryset=LegalEntity.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = Site
        fields = [
            "id",
            "code",
            "name",
            "status",
            "address_line1",
            "address_line2",
            "city",
            "state_province",
            "country",
            "postal_code",
            "latitude",
            "longitude",
            "timezone",
            "hours_per_day",
            "hours_per_week",
            "currency",
            "legal_entity",
            "tax_config",
            "erp_code",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        site = Site(
            pk=instance.pk if instance else None,
            code=attrs.get("code", getattr(instance, "code", None)),
            name=attrs.get("name", getattr(instance, "name", None)),
            status=attrs.get("status", getattr(instance, "status", Site.STATUS_ACTIVE)),
            address_line1=attrs.get("address_line1", getattr(instance, "address_line1", "")),
            address_line2=attrs.get("address_line2", getattr(instance, "address_line2", "")),
            city=attrs.get("city", getattr(instance, "city", "")),
            state_province=attrs.get("state_province", getattr(instance, "state_province", "")),
            country=attrs.get("country", getattr(instance, "country", "")),
            postal_code=attrs.get("postal_code", getattr(instance, "postal_code", "")),
            latitude=attrs.get("latitude", getattr(instance, "latitude", None)),
            longitude=attrs.get("longitude", getattr(instance, "longitude", None)),
            timezone=attrs.get("timezone", getattr(instance, "timezone", "")),
            hours_per_day=attrs.get("hours_per_day", getattr(instance, "hours_per_day", None)),
            hours_per_week=attrs.get("hours_per_week", getattr(instance, "hours_per_week", None)),
            currency=attrs.get("currency", getattr(instance, "currency", "USD")),
            legal_entity=attrs.get("legal_entity", getattr(instance, "legal_entity", None)),
            tax_config=attrs.get("tax_config", getattr(instance, "tax_config", {})),
            erp_code=attrs.get("erp_code", getattr(instance, "erp_code", "")),
        )
        try:
            site.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["country"] = site.country
        attrs["currency"] = site.currency
        return attrs


class SupplierSerializer(serializers.ModelSerializer):
    supplier_id = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Supplier
        fields = [
            "id",
            "supplier_id",
            "supplier_code",
            "name",
            "email",
            "contact_name",
            "contact_email",
            "contact_phone",
            "tax_id",
            "diversity_status",
            "supplier_type",
            "category",
            "active_workers",
            "active_sows",
            "owner_name",
            "status",
            "risk_level",
            "compliance_status",
        ]
        read_only_fields = ["supplier_id", "supplier_code"]

    def get_supplier_id(self, obj):
        if obj.supplier_code:
            return obj.supplier_code
        return f"SUP-{obj.id:05d}"


class SupplierInviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    expires_in_days = serializers.IntegerField(required=False, min_value=1, max_value=30, default=7)


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
