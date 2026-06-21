from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from apps.policies.models import (
    WorkerLifecycleWorkflow,
    WorkflowBlock,
    WorkflowBlockRequirement,
    WorkflowPolicyScope,
    WorkflowPolicyScopeField,
    WorkflowRequirement,
)


class WorkflowPolicyScopeFieldSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField(read_only=True)
    display = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = WorkflowPolicyScopeField
        fields = [
            "id",
            "sequence",
            "field_key",
            "operator",
            "location",
            "cost_center",
            "business_unit",
            "role_definition",
            "supplier",
            "label",
            "display",
        ]
        read_only_fields = ["id", "label", "display"]

    def get_label(self, obj):
        return dict(WorkflowPolicyScopeField.FIELD_CHOICES).get(obj.field_key, obj.field_key)

    def get_display(self, obj):
        target = obj.target()
        return str(target) if target is not None else ""

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        field_key = attrs.get("field_key", getattr(instance, "field_key", ""))
        field_key = (field_key or "").strip().lower()
        attrs["field_key"] = field_key
        attrs["operator"] = (attrs.get("operator", getattr(instance, "operator", "equals")) or "equals").strip().lower()

        field_to_attr = {
            WorkflowPolicyScopeField.FIELD_LOCATION: "location",
            WorkflowPolicyScopeField.FIELD_COST_CENTER: "cost_center",
            WorkflowPolicyScopeField.FIELD_BUSINESS_UNIT: "business_unit",
            WorkflowPolicyScopeField.FIELD_ROLE: "role_definition",
            WorkflowPolicyScopeField.FIELD_SUPPLIER: "supplier",
        }
        expected_attr = field_to_attr.get(field_key)
        if not expected_attr:
            raise serializers.ValidationError({"field_key": "Unsupported scope field."})

        values = {}
        for attr_name in field_to_attr.values():
            values[attr_name] = attrs.get(attr_name, getattr(instance, attr_name, None))

        populated = [attr_name for attr_name, value in values.items() if value is not None]
        if populated != [expected_attr]:
            raise serializers.ValidationError(
                {
                    expected_attr: (
                        f"{expected_attr} is required for {field_key} scope fields, "
                        "and no other scope target may be set."
                    )
                }
            )

        for attr_name in field_to_attr.values():
            if attr_name != expected_attr:
                attrs[attr_name] = None

        return attrs


class WorkflowPolicyScopeSerializer(serializers.ModelSerializer):
    fields = WorkflowPolicyScopeFieldSerializer(many=True, required=False)

    class Meta:
        model = WorkflowPolicyScope
        fields = [
            "id",
            "worker_type",
            "fields",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        worker_type = attrs.get("worker_type")
        if worker_type is not None:
            attrs["worker_type"] = worker_type.strip().lower()

        fields = attrs.get("fields")
        if fields is not None:
            sequences = [item.get("sequence", index + 1) for index, item in enumerate(fields)]
            if len(sequences) != len(set(sequences)):
                raise serializers.ValidationError({"fields": "Scope field sequence values must be unique."})

            field_keys = [item.get("field_key") for item in fields]
            if len(field_keys) != len(set(field_keys)):
                raise serializers.ValidationError({"fields": "Scope field keys must be unique."})

        return attrs


class WorkflowRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowRequirement
        fields = [
            "id",
            "code",
            "name",
            "description",
            "default_owner",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        requirement = WorkflowRequirement(
            pk=instance.pk if instance else None,
            code=attrs.get("code", getattr(instance, "code", "")),
            name=attrs.get("name", getattr(instance, "name", "")),
            description=attrs.get("description", getattr(instance, "description", "")),
            default_owner=attrs.get("default_owner", getattr(instance, "default_owner", WorkflowRequirement.OWNER_WORKER)),
            is_active=attrs.get("is_active", getattr(instance, "is_active", True)),
        )
        try:
            requirement.full_clean(exclude=["tenant_id", "created_at", "updated_at"])
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise serializers.ValidationError(exc.message_dict)
            raise serializers.ValidationError({"detail": exc.messages})

        attrs["code"] = requirement.code
        attrs["name"] = requirement.name
        attrs["description"] = requirement.description
        return attrs


class WorkflowBlockRequirementSerializer(serializers.ModelSerializer):
    requirement_name = serializers.CharField(source="requirement.name", read_only=True)

    class Meta:
        model = WorkflowBlockRequirement
        fields = [
            "id",
            "sequence",
            "requirement",
            "requirement_name",
            "name",
            "owner",
            "config",
        ]
        read_only_fields = ["id", "requirement_name"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        sequence = attrs.get("sequence", getattr(instance, "sequence", 1))
        requirement = attrs.get("requirement", getattr(instance, "requirement", None))
        name = (attrs.get("name", getattr(instance, "name", "")) or "").strip()
        config = attrs.get("config", getattr(instance, "config", {}))

        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if requirement and not name:
            name = requirement.name
        if not name:
            raise serializers.ValidationError({"name": "Requirement name is required."})
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise serializers.ValidationError({"config": "Config must be an object."})

        attrs["name"] = name
        attrs["config"] = config
        return attrs


class WorkflowBlockSerializer(serializers.ModelSerializer):
    requirements = WorkflowBlockRequirementSerializer(many=True, required=False)

    class Meta:
        model = WorkflowBlock
        fields = [
            "id",
            "sequence",
            "block_type",
            "name",
            "gate_type",
            "integration_type",
            "config",
            "requirements",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        sequence = attrs.get("sequence", getattr(instance, "sequence", 1))
        block_type = (attrs.get("block_type", getattr(instance, "block_type", "")) or "").strip().lower()
        name = (attrs.get("name", getattr(instance, "name", "")) or "").strip()
        integration_type = (
            attrs.get("integration_type", getattr(instance, "integration_type", "")) or ""
        ).strip().lower()
        config = attrs.get("config", getattr(instance, "config", {}))

        if sequence < 1:
            raise serializers.ValidationError({"sequence": "Sequence must be greater than or equal to 1."})
        if block_type not in {WorkflowBlock.TYPE_REQUIREMENT, WorkflowBlock.TYPE_SYSTEM}:
            raise serializers.ValidationError({"block_type": "Unsupported block type."})
        if not name:
            raise serializers.ValidationError({"name": "This field may not be blank."})
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise serializers.ValidationError({"config": "Config must be an object."})
        if block_type == WorkflowBlock.TYPE_SYSTEM and not integration_type:
            raise serializers.ValidationError({"integration_type": "Integration type is required for system blocks."})
        if block_type == WorkflowBlock.TYPE_REQUIREMENT:
            integration_type = ""

        requirements = attrs.get("requirements")
        if requirements is not None:
            requirement_sequences = [item.get("sequence", index + 1) for index, item in enumerate(requirements)]
            if len(requirement_sequences) != len(set(requirement_sequences)):
                raise serializers.ValidationError({"requirements": "Requirement sequence values must be unique."})

        attrs["block_type"] = block_type
        attrs["name"] = name
        attrs["integration_type"] = integration_type
        attrs["config"] = config
        return attrs


class WorkerLifecycleWorkflowSerializer(serializers.ModelSerializer):
    policy_scope = WorkflowPolicyScopeSerializer(required=False)
    blocks = WorkflowBlockSerializer(many=True, required=False)
    health = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = WorkerLifecycleWorkflow
        fields = [
            "id",
            "tenant_id",
            "name",
            "workflow_type",
            "status",
            "is_active",
            "version",
            "policy_scope",
            "blocks",
            "health",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "version", "health", "created_by", "created_at", "updated_at"]

    def get_health(self, obj):
        blocks = list(obj.blocks.all())
        requirements_by_block = {}
        for block in blocks:
            requirements_by_block[block.id] = list(block.requirements.all())

        requirement_count = sum(len(items) for items in requirements_by_block.values())
        hard_gate_count = sum(1 for block in blocks if block.gate_type == WorkflowBlock.GATE_HARD)
        soft_gate_count = sum(1 for block in blocks if block.gate_type == WorkflowBlock.GATE_SOFT)
        system_block_count = sum(1 for block in blocks if block.block_type == WorkflowBlock.TYPE_SYSTEM)

        checks = {
            "policy_name_set": bool((obj.name or "").strip()),
            "at_least_one_step": bool(blocks),
            "no_block_issues": self._has_no_block_issues(blocks, requirements_by_block),
            "no_circular_dependencies": True,
        }
        return {
            "status": "complete" if all(checks.values()) else "incomplete",
            "checks": checks,
            "counts": {
                "steps": len(blocks),
                "requirements": requirement_count,
                "hard_gates": hard_gate_count,
                "soft_gates": soft_gate_count,
                "system_blocks": system_block_count,
            },
        }

    @staticmethod
    def _has_no_block_issues(blocks, requirements_by_block):
        for block in blocks:
            if block.block_type == WorkflowBlock.TYPE_REQUIREMENT and not requirements_by_block.get(block.id):
                return False
            if block.block_type == WorkflowBlock.TYPE_SYSTEM and not block.integration_type:
                return False
        return True

    def validate(self, attrs):
        name = attrs.get("name")
        if name is not None:
            name = name.strip()
            if not name:
                raise serializers.ValidationError({"name": "This field may not be blank."})
            attrs["name"] = name

        workflow_type = attrs.get("workflow_type")
        if workflow_type is not None:
            workflow_type = workflow_type.strip().lower()
            if workflow_type not in {WorkerLifecycleWorkflow.TYPE_ONBOARDING, WorkerLifecycleWorkflow.TYPE_OFFBOARDING}:
                raise serializers.ValidationError({"workflow_type": "Unsupported workflow type."})
            attrs["workflow_type"] = workflow_type

        blocks = attrs.get("blocks")
        if blocks is not None:
            block_sequences = [item.get("sequence", index + 1) for index, item in enumerate(blocks)]
            if len(block_sequences) != len(set(block_sequences)):
                raise serializers.ValidationError({"blocks": "Block sequence values must be unique."})

        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        policy_scope_data = validated_data.pop("policy_scope", None)
        blocks_data = validated_data.pop("blocks", None)

        if request is not None:
            validated_data["tenant_id"] = getattr(getattr(request, "tenant", None), "id", None)
            validated_data["created_by"] = request.user if request.user.is_authenticated else None

        with transaction.atomic():
            workflow = WorkerLifecycleWorkflow(**validated_data)
            workflow.full_clean()
            workflow.save()
            self._upsert_scope(workflow, policy_scope_data or {})
            if blocks_data is not None:
                self._replace_blocks(workflow, blocks_data)
        return workflow

    def update(self, instance, validated_data):
        policy_scope_data = validated_data.pop("policy_scope", None)
        blocks_data = validated_data.pop("blocks", None)

        with transaction.atomic():
            for key, value in validated_data.items():
                setattr(instance, key, value)
            instance.full_clean()
            instance.save()
            if policy_scope_data is not None:
                self._upsert_scope(instance, policy_scope_data)
            if blocks_data is not None:
                self._replace_blocks(instance, blocks_data)
        return instance

    @classmethod
    def _upsert_scope(cls, workflow, policy_scope_data):
        fields_data = policy_scope_data.pop("fields", None)
        scope, _ = WorkflowPolicyScope.objects.get_or_create(workflow=workflow)
        for key, value in policy_scope_data.items():
            setattr(scope, key, value)
        scope.full_clean()
        scope.save()

        if fields_data is not None:
            scope.fields.all().delete()
            for index, field_data in enumerate(fields_data, start=1):
                data = dict(field_data)
                data.setdefault("sequence", index)
                field = WorkflowPolicyScopeField(scope=scope, **data)
                field.full_clean()
                field.save()
        return scope

    @classmethod
    def _replace_blocks(cls, workflow, blocks_data):
        workflow.blocks.all().delete()
        for index, block_data in enumerate(blocks_data, start=1):
            data = dict(block_data)
            requirements_data = data.pop("requirements", [])
            data.setdefault("sequence", index)
            block = WorkflowBlock(workflow=workflow, **data)
            block.full_clean()
            block.save()

            if block.block_type == WorkflowBlock.TYPE_SYSTEM:
                continue

            for requirement_index, requirement_data in enumerate(requirements_data, start=1):
                req_data = dict(requirement_data)
                req_data.setdefault("sequence", requirement_index)
                requirement = WorkflowBlockRequirement(block=block, **req_data)
                requirement.full_clean()
                requirement.save()
