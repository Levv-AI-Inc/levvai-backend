from rest_framework import serializers

from apps.accounts.models import Membership
from apps.approvals.catalog import get_field_definition
from apps.approvals.engine import normalize_condition_value
from apps.approvals.models import ApprovalChain, ApprovalChainCondition, ApprovalChainStep
from apps.approvals.services import ApprovalChainService


class ApprovalChainConditionSerializer(serializers.ModelSerializer):
    value = serializers.JSONField(source="value_json", required=False, allow_null=True)
    field_label = serializers.SerializerMethodField(read_only=True)
    data_type = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ApprovalChainCondition
        fields = ["id", "sequence", "field_key", "field_label", "data_type", "operator", "value"]
        read_only_fields = ["id", "field_label", "data_type"]

    def get_field_label(self, obj):
        field_definition = get_field_definition(obj.field_key)
        return field_definition.label if field_definition else obj.field_key

    def get_data_type(self, obj):
        field_definition = get_field_definition(obj.field_key)
        return field_definition.data_type if field_definition else "dynamic"

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        field_key = attrs.get("field_key", getattr(instance, "field_key", None))
        operator = attrs.get("operator", getattr(instance, "operator", None))
        value = attrs.get("value_json", getattr(instance, "value_json", None))
        sequence = attrs.get("sequence", getattr(instance, "sequence", 1))

        field_definition = get_field_definition(field_key)
        if not field_definition:
            raise serializers.ValidationError({"field_key": "Unsupported field key."})
        if operator not in field_definition.supported_operators:
            raise serializers.ValidationError({"operator": "Operator is not supported for this field."})
        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})

        try:
            attrs["value_json"] = normalize_condition_value(field_definition, operator, value)
        except ValueError as exc:
            raise serializers.ValidationError({"value": str(exc)})

        return attrs


class ApprovalChainStepSerializer(serializers.ModelSerializer):
    approver_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ApprovalChainStep
        fields = ["id", "sequence", "step_type", "approver", "approver_name", "amount", "currency"]
        read_only_fields = ["id", "approver_name"]

    def get_approver_name(self, obj):
        return obj.approver.get_full_name().strip() or obj.approver.username

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        sequence = attrs.get("sequence", getattr(instance, "sequence", 1))
        step_type = attrs.get("step_type", getattr(instance, "step_type", ApprovalChainStep.TYPE_SPECIFIC_USER))
        approver = attrs.get("approver", getattr(instance, "approver", None))
        amount = attrs.get("amount", getattr(instance, "amount", None))
        currency = attrs.get("currency", getattr(instance, "currency", "USD"))

        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if step_type != ApprovalChainStep.TYPE_SPECIFIC_USER:
            raise serializers.ValidationError({"step_type": "Only specific_user steps are supported right now."})
        if approver is None:
            raise serializers.ValidationError({"approver": "Approver is required."})
        if amount is None or amount < 0:
            raise serializers.ValidationError({"amount": "Amount must be greater than or equal to 0."})

        tenant = getattr(self.context.get("request"), "tenant", None)
        if tenant and tenant.schema_name != "public":
            membership_exists = Membership.objects.filter(
                user=approver,
                tenant_id=tenant.id,
                status=Membership.STATUS_ACTIVE,
                is_active=True,
            ).exists()
            if not membership_exists:
                raise serializers.ValidationError({"approver": "Approver must be an active member of this tenant."})

        currency = (currency or "").upper()
        if len(currency) != 3:
            raise serializers.ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})
        attrs["currency"] = currency
        return attrs


class ApprovalChainSerializer(serializers.ModelSerializer):
    conditions = ApprovalChainConditionSerializer(many=True, required=False)
    steps = ApprovalChainStepSerializer(many=True, required=False)

    class Meta:
        model = ApprovalChain
        fields = [
            "id",
            "name",
            "description",
            "is_active",
            "priority",
            "match_strategy",
            "conditions",
            "steps",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        conditions = attrs.get("conditions")
        steps = attrs.get("steps")
        final_is_active = attrs.get("is_active", getattr(instance, "is_active", True))

        self._validate_unique_sequences("conditions", conditions)
        self._validate_unique_sequences("steps", steps)

        final_steps = steps
        if final_steps is None and instance is not None:
            final_steps = [
                {
                    "sequence": step.sequence,
                    "step_type": step.step_type,
                    "approver": step.approver,
                    "amount": step.amount,
                    "currency": step.currency,
                }
                for step in instance.steps.all()
            ]

        if not final_steps:
            raise serializers.ValidationError({"steps": "At least one approval step is required."})

        if final_is_active and not final_steps:
            raise serializers.ValidationError({"steps": "Active approval chains must have at least one approval step."})

        return attrs

    def _validate_unique_sequences(self, field_name, items):
        if items is None:
            return
        sequences = [item["sequence"] for item in items]
        if len(sequences) != len(set(sequences)):
            raise serializers.ValidationError({field_name: "Sequence values must be unique."})

    def create(self, validated_data):
        conditions = validated_data.pop("conditions", [])
        steps = validated_data.pop("steps", [])
        request = self.context["request"]
        return ApprovalChainService.create_chain(
            tenant=request.tenant,
            user=request.user,
            attrs=validated_data,
            conditions=conditions,
            steps=steps,
        )

    def update(self, instance, validated_data):
        conditions = validated_data.pop("conditions", None)
        steps = validated_data.pop("steps", None)
        request = self.context["request"]
        return ApprovalChainService.update_chain(
            tenant=request.tenant,
            user=request.user,
            chain=instance,
            attrs=validated_data,
            conditions=conditions,
            steps=steps,
        )


class ApprovalChainSimulationSerializer(serializers.Serializer):
    payload = serializers.JSONField()
    include_inactive = serializers.BooleanField(required=False, default=False)
    include_non_matches = serializers.BooleanField(required=False, default=False)
