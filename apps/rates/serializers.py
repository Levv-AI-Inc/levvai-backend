from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.rates.calculations import (
    get_rule_field_definition,
    normalize_rule_condition_value,
)
from apps.rates.models import (
    RateCard,
    RateCardLine,
    RateCardLineValue,
    RateRule,
    RateRuleCondition,
    RateStructure,
    RateStructureComponent,
)
from apps.rates.services import RateCardService, RateRuleService, RateStructureService


class RateStructureComponentSerializer(serializers.ModelSerializer):
    code = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = RateStructureComponent
        fields = [
            "id",
            "sequence",
            "code",
            "label",
            "value_type",
            "calculation_role",
            "is_required",
            "is_active",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        sequence = attrs.get("sequence", getattr(self.instance, "sequence", 1))
        label = (attrs.get("label", getattr(self.instance, "label", "")) or "").strip()
        code = (attrs.get("code", getattr(self.instance, "code", "")) or "").strip()
        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if not label:
            raise serializers.ValidationError({"label": "This field may not be blank."})
        attrs["label"] = label
        attrs["code"] = code
        return attrs


class RateStructureListSerializer(serializers.ModelSerializer):
    component_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = RateStructure
        fields = [
            "id",
            "name",
            "description",
            "status",
            "currency_mode",
            "rounding_scale",
            "is_default",
            "component_count",
            "created_at",
            "updated_at",
        ]

    def get_component_count(self, obj):
        return obj.components.count()


class RateStructureSerializer(serializers.ModelSerializer):
    components = RateStructureComponentSerializer(many=True, required=False)

    class Meta:
        model = RateStructure
        fields = [
            "id",
            "name",
            "description",
            "status",
            "currency_mode",
            "rounding_scale",
            "is_default",
            "components",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        components = attrs.get("components")
        final_status = attrs.get("status", getattr(instance, "status", RateStructure.STATUS_DRAFT))

        final_components = components
        if final_components is None and instance is not None:
            final_components = [
                {
                    "sequence": component.sequence,
                    "code": component.code,
                    "label": component.label,
                    "value_type": component.value_type,
                    "calculation_role": component.calculation_role,
                    "is_required": component.is_required,
                    "is_active": component.is_active,
                }
                for component in instance.components.all()
            ]

        if final_components is not None:
            self._validate_component_payload(final_components, require_exactly_one_base=final_status == RateStructure.STATUS_ACTIVE)
        elif final_status == RateStructure.STATUS_ACTIVE and instance is None:
            raise serializers.ValidationError({"components": "Active rate structures must include components."})

        if final_status == RateStructure.STATUS_ACTIVE and not final_components:
            raise serializers.ValidationError({"components": "Active rate structures must include components."})

        structure = RateStructure(
            pk=instance.pk if instance else None,
            name=attrs.get("name", getattr(instance, "name", "")),
            description=attrs.get("description", getattr(instance, "description", "")),
            status=final_status,
            currency_mode=attrs.get("currency_mode", getattr(instance, "currency_mode", RateStructure.CURRENCY_MODE_SINGLE)),
            rounding_scale=attrs.get("rounding_scale", getattr(instance, "rounding_scale", 2)),
            is_default=attrs.get("is_default", getattr(instance, "is_default", False)),
        )
        if instance is not None:
            structure._state.adding = False
            structure._state.db = instance._state.db
        try:
            structure.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["name"] = structure.name
        attrs["description"] = structure.description
        return attrs

    def _validate_component_payload(self, components, *, require_exactly_one_base):
        sequences = [item["sequence"] for item in components]
        if len(sequences) != len(set(sequences)):
            raise serializers.ValidationError({"components": "Component sequence values must be unique."})

        explicit_codes = [item["code"] for item in components if item.get("code")]
        if len(explicit_codes) != len(set(explicit_codes)):
            raise serializers.ValidationError({"components": "Component codes must be unique."})

        base_count = sum(1 for item in components if item["calculation_role"] == RateStructureComponent.ROLE_BASE)
        if base_count > 1:
            raise serializers.ValidationError({"components": "Rate structures can include at most one base component."})
        if require_exactly_one_base and components and base_count != 1:
            raise serializers.ValidationError({"components": "Active rate structures must include exactly one base component."})

    def create(self, validated_data):
        components = validated_data.pop("components", [])
        request = self.context["request"]
        return RateStructureService.create_structure(
            tenant=request.tenant,
            user=request.user,
            attrs=validated_data,
            components=components,
        )

    def update(self, instance, validated_data):
        components = validated_data.pop("components", None)
        request = self.context["request"]
        return RateStructureService.update_structure(
            tenant=request.tenant,
            user=request.user,
            structure=instance,
            attrs=validated_data,
            components=components,
        )


class RateCardLineValueSerializer(serializers.ModelSerializer):
    component_code = serializers.CharField(source="rate_structure_component.code", read_only=True)
    component_label = serializers.CharField(source="rate_structure_component.label", read_only=True)
    value_type = serializers.CharField(source="rate_structure_component.value_type", read_only=True)

    class Meta:
        model = RateCardLineValue
        fields = [
            "id",
            "rate_structure_component",
            "component_code",
            "component_label",
            "value_type",
            "numeric_value",
        ]
        read_only_fields = ["id", "component_code", "component_label", "value_type"]

    def validate(self, attrs):
        numeric_value = attrs.get("numeric_value", getattr(self.instance, "numeric_value", None))
        if numeric_value is None or numeric_value < 0:
            raise serializers.ValidationError({"numeric_value": "Numeric value must be greater than or equal to 0."})
        return attrs


class RateCardLineSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    component_values = RateCardLineValueSerializer(many=True, required=False)

    class Meta:
        model = RateCardLine
        fields = [
            "id",
            "sequence",
            "supplier",
            "supplier_name",
            "location_label",
            "bill_rate",
            "component_values",
        ]
        read_only_fields = ["id", "supplier_name", "bill_rate"]

    def validate(self, attrs):
        sequence = attrs.get("sequence", getattr(self.instance, "sequence", 1))
        location_label = (attrs.get("location_label", getattr(self.instance, "location_label", "")) or "").strip()
        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        attrs["location_label"] = location_label
        return attrs


class RateCardListSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role_definition.name", read_only=True)
    rate_structure_name = serializers.CharField(source="rate_structure.name", read_only=True)

    class Meta:
        model = RateCard
        fields = [
            "id",
            "name",
            "role_definition",
            "role_name",
            "currency",
            "unit",
            "effective_date",
            "end_date",
            "rate_structure",
            "rate_structure_name",
            "status",
            "created_at",
            "updated_at",
        ]


class RateCardSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role_definition.name", read_only=True)
    rate_structure_name = serializers.CharField(source="rate_structure.name", read_only=True)
    rate_structure_components = serializers.SerializerMethodField(read_only=True)
    lines = RateCardLineSerializer(many=True, required=False)

    class Meta:
        model = RateCard
        fields = [
            "id",
            "name",
            "role_definition",
            "role_name",
            "currency",
            "unit",
            "effective_date",
            "end_date",
            "rate_structure",
            "rate_structure_name",
            "rate_structure_components",
            "status",
            "notes",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "role_name", "rate_structure_name", "rate_structure_components", "created_at", "updated_at"]

    def get_rate_structure_components(self, obj):
        components = obj.rate_structure.components.all().order_by("sequence", "id")
        return RateStructureComponentSerializer(components, many=True).data

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        lines = attrs.get("lines")
        final_status = attrs.get("status", getattr(instance, "status", RateCard.STATUS_DRAFT))
        final_rate_structure = attrs.get("rate_structure", getattr(instance, "rate_structure", None))

        if instance and "rate_structure" in attrs and lines is None:
            raise serializers.ValidationError(
                {"lines": "When changing the rate structure, you must submit the full lines payload for recalculation."}
            )

        final_lines = lines
        if final_lines is None and instance is not None:
            final_lines = list(instance.lines.all())

        if final_status == RateCard.STATUS_ACTIVE and not final_lines:
            raise serializers.ValidationError({"lines": "Active rate cards must include at least one line."})

        if lines is not None:
            self._validate_line_payload(lines, final_rate_structure)

        card = RateCard(
            pk=instance.pk if instance else None,
            name=attrs.get("name", getattr(instance, "name", "")),
            role_definition=attrs.get("role_definition", getattr(instance, "role_definition", None)),
            currency=attrs.get("currency", getattr(instance, "currency", "")),
            unit=attrs.get("unit", getattr(instance, "unit", RateCard.UNIT_HOUR)),
            effective_date=attrs.get("effective_date", getattr(instance, "effective_date", None)),
            end_date=attrs.get("end_date", getattr(instance, "end_date", None)),
            rate_structure=final_rate_structure,
            status=final_status,
            notes=attrs.get("notes", getattr(instance, "notes", "")),
        )
        if instance is not None:
            card._state.adding = False
            card._state.db = instance._state.db
        try:
            card.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["name"] = card.name
        attrs["currency"] = card.currency
        attrs["notes"] = card.notes
        return attrs

    def _validate_line_payload(self, lines, rate_structure):
        sequences = [item["sequence"] for item in lines]
        if len(sequences) != len(set(sequences)):
            raise serializers.ValidationError({"lines": "Line sequence values must be unique."})

        supplier_locations = []
        for line in lines:
            supplier = line["supplier"]
            location_label = line.get("location_label", "")
            supplier_locations.append((supplier.pk, location_label))

            component_values = line.get("component_values", [])
            component_ids = [value["rate_structure_component"].pk for value in component_values]
            if len(component_ids) != len(set(component_ids)):
                raise serializers.ValidationError(
                    {"lines": f"Duplicate component values are not allowed for supplier '{supplier.name}'."}
                )

            if rate_structure is not None:
                if any(
                    value["rate_structure_component"].rate_structure_id != rate_structure.id
                    for value in component_values
                ):
                    raise serializers.ValidationError(
                        {"lines": "All line component values must reference components from the selected rate structure."}
                    )

        if len(supplier_locations) != len(set(supplier_locations)):
            raise serializers.ValidationError({"lines": "Supplier and location combinations must be unique."})

    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        request = self.context["request"]
        return RateCardService.create_card(
            tenant=request.tenant,
            user=request.user,
            attrs=validated_data,
            lines=lines,
        )

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        request = self.context["request"]
        return RateCardService.update_card(
            tenant=request.tenant,
            user=request.user,
            card=instance,
            attrs=validated_data,
            lines=lines,
        )


class RateRuleConditionSerializer(serializers.ModelSerializer):
    value = serializers.JSONField(source="value_json")
    field_label = serializers.SerializerMethodField(read_only=True)
    data_type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = RateRuleCondition
        fields = [
            "id",
            "sequence",
            "joiner",
            "field_key",
            "field_label",
            "data_type",
            "operator",
            "value",
        ]
        read_only_fields = ["id", "field_label", "data_type"]

    def get_field_label(self, obj):
        definition = get_rule_field_definition(obj.field_key)
        return definition.label if definition else obj.field_key

    def get_data_type(self, obj):
        definition = get_rule_field_definition(obj.field_key)
        return definition.data_type if definition else "unknown"

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        field_key = attrs.get("field_key", getattr(instance, "field_key", None))
        operator = attrs.get("operator", getattr(instance, "operator", None))
        value = attrs.get("value_json", getattr(instance, "value_json", None))
        sequence = attrs.get("sequence", getattr(instance, "sequence", 1))

        definition = get_rule_field_definition(field_key)
        if not definition:
            raise serializers.ValidationError({"field_key": "Unsupported rule field key."})
        if operator not in definition.supported_operators:
            raise serializers.ValidationError({"operator": "Operator is not supported for this field."})
        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})

        try:
            attrs["value_json"] = normalize_rule_condition_value(definition, operator, value)
        except ValueError as exc:
            raise serializers.ValidationError({"value": str(exc)})

        return attrs


class RateRuleListSerializer(serializers.ModelSerializer):
    rate_structure_name = serializers.CharField(source="rate_structure.name", read_only=True)
    role_name = serializers.CharField(source="role_definition.name", read_only=True)

    class Meta:
        model = RateRule
        fields = [
            "id",
            "name",
            "priority",
            "status",
            "rate_structure",
            "rate_structure_name",
            "role_definition",
            "role_name",
            "effective_date",
            "end_date",
            "action_type",
            "action_value",
            "stop_processing",
            "created_at",
            "updated_at",
        ]


class RateRuleSerializer(serializers.ModelSerializer):
    rate_structure_name = serializers.CharField(source="rate_structure.name", read_only=True)
    role_name = serializers.CharField(source="role_definition.name", read_only=True)
    conditions = RateRuleConditionSerializer(many=True, required=False)

    class Meta:
        model = RateRule
        fields = [
            "id",
            "name",
            "description",
            "priority",
            "status",
            "rate_structure",
            "rate_structure_name",
            "role_definition",
            "role_name",
            "effective_date",
            "end_date",
            "action_type",
            "action_value",
            "stop_processing",
            "conditions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "rate_structure_name", "role_name", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        conditions = attrs.get("conditions")
        final_status = attrs.get("status", getattr(instance, "status", RateRule.STATUS_DRAFT))

        final_conditions = conditions
        if final_conditions is None and instance is not None:
            final_conditions = list(instance.conditions.all())

        if final_status == RateRule.STATUS_ACTIVE and instance is None and conditions is None:
            raise serializers.ValidationError({"conditions": "Active rate rules must include at least one condition."})

        if final_status == RateRule.STATUS_ACTIVE and not final_conditions:
            raise serializers.ValidationError({"conditions": "Active rate rules must include at least one condition."})

        if conditions is not None:
            sequences = [item["sequence"] for item in conditions]
            if len(sequences) != len(set(sequences)):
                raise serializers.ValidationError({"conditions": "Condition sequence values must be unique."})

        rule = RateRule(
            pk=instance.pk if instance else None,
            name=attrs.get("name", getattr(instance, "name", "")),
            description=attrs.get("description", getattr(instance, "description", "")),
            priority=attrs.get("priority", getattr(instance, "priority", 100)),
            status=final_status,
            rate_structure=attrs.get("rate_structure", getattr(instance, "rate_structure", None)),
            role_definition=attrs.get("role_definition", getattr(instance, "role_definition", None)),
            effective_date=attrs.get("effective_date", getattr(instance, "effective_date", None)),
            end_date=attrs.get("end_date", getattr(instance, "end_date", None)),
            action_type=attrs.get("action_type", getattr(instance, "action_type", RateRule.ACTION_MULTIPLY_BILL_RATE)),
            action_value=attrs.get("action_value", getattr(instance, "action_value", None)),
            stop_processing=attrs.get("stop_processing", getattr(instance, "stop_processing", True)),
        )
        if instance is not None:
            rule._state.adding = False
            rule._state.db = instance._state.db
        try:
            rule.full_clean(exclude=["created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["name"] = rule.name
        attrs["description"] = rule.description
        return attrs

    def create(self, validated_data):
        conditions = validated_data.pop("conditions", [])
        request = self.context["request"]
        return RateRuleService.create_rule(
            tenant=request.tenant,
            user=request.user,
            attrs=validated_data,
            conditions=conditions,
        )

    def update(self, instance, validated_data):
        conditions = validated_data.pop("conditions", None)
        request = self.context["request"]
        return RateRuleService.update_rule(
            tenant=request.tenant,
            user=request.user,
            rule=instance,
            attrs=validated_data,
            conditions=conditions,
        )


class RateStructureCloneSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)


class RateStructurePreviewSerializer(serializers.Serializer):
    component_values = serializers.JSONField()

    def validate_component_values(self, value):
        if isinstance(value, dict):
            return value

        if isinstance(value, list):
            for index, item in enumerate(value):
                if not isinstance(item, dict):
                    raise serializers.ValidationError(
                        f"List item at index {index} must be an object with rate_structure_component and numeric_value."
                    )
                if "rate_structure_component" not in item:
                    raise serializers.ValidationError(
                        f"List item at index {index} is missing rate_structure_component."
                    )
                if "numeric_value" not in item and "value" not in item:
                    raise serializers.ValidationError(
                        f"List item at index {index} is missing numeric_value."
                    )
            return value

        raise serializers.ValidationError(
            "Component values must be an object keyed by component code, or a list of component/value entries."
        )
        return value


class RateRulePreviewSerializer(serializers.Serializer):
    base_bill_rate = serializers.DecimalField(max_digits=14, decimal_places=4)
    context = serializers.JSONField(required=False, default=dict)

    def validate_context(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Context must be an object.")
        return value
