from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.masterdata.models import (
    BusinessUnit,
    Company,
    CostCenter,
    CustomField,
    JobTemplate,
    LegalEntity,
    Location,
    RateCard,
    RoleDefinition,
    Site,
    Supplier,
    SupplierCoverage,
)
from apps.workorders.models import WorkOrder


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


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = [
            "id",
            "name",
            "country",
            "region",
            "status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        location = Location(
            pk=instance.pk if instance else None,
            name=attrs.get("name", getattr(instance, "name", "")),
            country=attrs.get("country", getattr(instance, "country", "")),
            region=attrs.get("region", getattr(instance, "region", "")),
            status=attrs.get("status", getattr(instance, "status", Location.STATUS_ACTIVE)),
        )
        try:
            location.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["name"] = location.name
        attrs["country"] = location.country
        attrs["region"] = location.region
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

    SOURCE_OWNED_FIELDS = {
        "source_system",
        "source_identifier",
        "source_status",
        "source_last_synced_at",
        "registered_address",
        "hq_country",
        "buying_entities",
        "business_units",
        "service_type",
    }

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

    def to_internal_value(self, data):
        attempted_source_fields = self.SOURCE_OWNED_FIELDS.intersection(data.keys())
        if attempted_source_fields:
            raise serializers.ValidationError(
                {
                    field: ["This field is owned by the source system and is read-only."]
                    for field in sorted(attempted_source_fields)
                }
            )
        return super().to_internal_value(data)


class SupplierDetailSerializer(SupplierSerializer):
    source_ownership = serializers.SerializerMethodField(read_only=True)
    allowed_actions = serializers.SerializerMethodField(read_only=True)

    class Meta(SupplierSerializer.Meta):
        fields = SupplierSerializer.Meta.fields + [
            "source_system",
            "source_identifier",
            "source_status",
            "source_last_synced_at",
            "registered_address",
            "hq_country",
            "buying_entities",
            "business_units",
            "service_type",
            "active_in_levv",
            "source_ownership",
            "allowed_actions",
        ]
        read_only_fields = SupplierSerializer.Meta.read_only_fields + [
            "source_system",
            "source_identifier",
            "source_status",
            "source_last_synced_at",
            "registered_address",
            "hq_country",
            "buying_entities",
            "business_units",
            "service_type",
            "active_in_levv",
            "source_ownership",
            "allowed_actions",
        ]

    def get_source_ownership(self, obj):
        return {
            "owner": "source_system" if obj.source_system else "unconfigured",
            "read_only": True,
            "fields": sorted(self.SOURCE_OWNED_FIELDS),
        }

    def get_allowed_actions(self, obj):
        return ["view_overview", "view_workers"]


class SupplierWorkerSerializer(serializers.ModelSerializer):
    assignment_id = serializers.IntegerField(source="id", read_only=True)
    assignment_number = serializers.CharField(source="work_order_number", read_only=True)
    role_id = serializers.IntegerField(source="role_definition_id", read_only=True)
    role_name = serializers.CharField(source="role_definition.name", read_only=True)
    site_id = serializers.IntegerField(read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            "assignment_id",
            "assignment_number",
            "worker_full_name",
            "worker_email",
            "worker_phone",
            "status",
            "role_id",
            "role_name",
            "site_id",
            "site_name",
            "work_location_label",
            "start_date",
            "end_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SupplierCoverageSerializer(serializers.ModelSerializer):
    supplier_id = serializers.IntegerField(read_only=True)
    role_id = serializers.IntegerField(read_only=True)
    role_code = serializers.CharField(source="role.code", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    role_location_label = serializers.CharField(source="role.location_label", read_only=True)
    site_id = serializers.IntegerField(read_only=True)
    site_code = serializers.CharField(source="site.code", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    site_city = serializers.CharField(source="site.city", read_only=True)
    site_country = serializers.CharField(source="site.country", read_only=True)

    class Meta:
        model = SupplierCoverage
        fields = [
            "id",
            "supplier_id",
            "role_id",
            "role_code",
            "role_name",
            "role_location_label",
            "site_id",
            "site_code",
            "site_name",
            "site_city",
            "site_country",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SupplierCoverageCreateSerializer(serializers.Serializer):
    role_id = serializers.PrimaryKeyRelatedField(
        source="role",
        queryset=RoleDefinition.objects.all(),
    )
    site_id = serializers.PrimaryKeyRelatedField(
        source="site",
        queryset=Site.objects.all(),
    )

    def validate_role_id(self, role):
        if not role.is_active:
            raise serializers.ValidationError("Coverage requires an active role.")
        return role

    def validate_site_id(self, site):
        if site.status != Site.STATUS_ACTIVE:
            raise serializers.ValidationError("Coverage requires an active site.")
        return site


class SupplierCoverageUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField(required=True)


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


class RoleDefinitionSerializer(serializers.ModelSerializer):
    location_label = serializers.SerializerMethodField(read_only=True)
    code = serializers.CharField(read_only=True)

    class Meta:
        model = RoleDefinition
        fields = [
            "id",
            "code",
            "name",
            "description",
            "country",
            "region",
            "city",
            "location_label",
            "default_currency",
            "default_unit",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "code", "location_label", "created_at", "updated_at"]

    def get_location_label(self, obj):
        return obj.location_label

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        role = RoleDefinition(
            pk=instance.pk if instance else None,
            code=getattr(instance, "code", ""),
            name=attrs.get("name", getattr(instance, "name", "")),
            description=attrs.get("description", getattr(instance, "description", "")),
            country=attrs.get("country", getattr(instance, "country", "")),
            region=attrs.get("region", getattr(instance, "region", "")),
            city=attrs.get("city", getattr(instance, "city", "")),
            default_currency=attrs.get("default_currency", getattr(instance, "default_currency", "USD")),
            default_unit=attrs.get("default_unit", getattr(instance, "default_unit", RoleDefinition.UNIT_HOUR)),
            is_active=attrs.get("is_active", getattr(instance, "is_active", True)),
        )
        try:
            role.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["name"] = role.name
        attrs["description"] = role.description
        attrs["code"] = role.code
        attrs["country"] = role.country
        attrs["region"] = role.region
        attrs["city"] = role.city
        attrs["default_currency"] = role.default_currency
        return attrs
