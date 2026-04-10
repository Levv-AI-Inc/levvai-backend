from rest_framework import serializers

from apps.intake.models import IntakeRequest


class IntakeRequestWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntakeRequest
        fields = [
            "engagement_type",
            "cost_center",
            "site",
            "supplier",
            "title",
            "description",
            "start_date",
            "end_date",
            "worker_count",
            "target_rate",
            "rate_unit",
            "budget_amount",
            "currency",
            "custom_fields",
        ]


class IntakeRequestDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntakeRequest
        fields = [
            "id",
            "tenant_id",
            "created_by",
            "created_at",
            "updated_at",
            "status",
            "engagement_type",
            "cost_center",
            "site",
            "supplier",
            "title",
            "description",
            "start_date",
            "end_date",
            "worker_count",
            "target_rate",
            "rate_unit",
            "budget_amount",
            "currency",
            "custom_fields",
            "submitted_at",
            "submitted_by",
            "decision_at",
            "decided_by",
            "decision_reason",
        ]


class IntakeDecisionSerializer(serializers.Serializer):
    decision_reason = serializers.CharField(required=False, allow_blank=True, default="")


class NovaConfidenceRequestSerializer(serializers.Serializer):
    intake_id = serializers.IntegerField()
