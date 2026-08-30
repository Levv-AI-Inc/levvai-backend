from rest_framework import serializers

from apps.timesheets.models import Timesheet, TimesheetEvent, TimesheetLine


class TimesheetLineSerializer(serializers.ModelSerializer):
    costCenter = serializers.CharField(source="cost_center_code", read_only=True)
    taskCode = serializers.CharField(source="task_code", read_only=True)

    class Meta:
        model = TimesheetLine
        fields = [
            "id",
            "line_date",
            "task_name",
            "hours",
            "cost_center_id",
            "cost_center_code",
            "cost_center_name",
            "costCenter",
            "task_code",
            "taskCode",
            "allocation_rationale",
            "rate_category",
            "bill_amount",
            "pay_amount",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "bill_amount", "pay_amount", "created_at", "updated_at"]


class TimesheetLineWriteSerializer(serializers.Serializer):
    line_date = serializers.DateField()
    task_name = serializers.CharField(max_length=255)
    hours = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0)
    cost_center_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    cost_center_code = serializers.CharField(required=False, allow_blank=True, max_length=200)
    cost_center_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    costCenter = serializers.CharField(required=False, allow_blank=True, max_length=200)
    task_code = serializers.CharField(required=False, allow_blank=True, max_length=100)
    taskCode = serializers.CharField(required=False, allow_blank=True, max_length=100)
    allocation_rationale = serializers.CharField(required=False, allow_blank=True, max_length=500)
    rationale = serializers.CharField(required=False, allow_blank=True, max_length=500)
    rate_category = serializers.CharField(required=False, allow_blank=True, max_length=64)

    def validate_task_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Task name is required.")
        return value


class TimesheetWriteSerializer(serializers.Serializer):
    worker_engagement_id = serializers.IntegerField(required=False, min_value=1)
    period_start = serializers.DateField(required=False)
    period_end = serializers.DateField(required=False)
    currency = serializers.CharField(required=False, allow_blank=True, max_length=3)
    comment = serializers.CharField(required=False, allow_blank=True)
    anomaly_reason = serializers.CharField(required=False, allow_blank=True)
    qa_issues = serializers.ListField(child=serializers.JSONField(), required=False)
    jurisdiction_flags = serializers.ListField(child=serializers.JSONField(), required=False)
    approval_brief = serializers.JSONField(required=False)
    lines = TimesheetLineWriteSerializer(many=True, required=False)

    def validate_currency(self, value):
        value = value.strip().upper()
        if value and len(value) != 3:
            raise serializers.ValidationError("Currency must be a 3-letter ISO 4217 code.")
        return value

    def validate(self, attrs):
        instance = self.context.get("instance")
        if not instance and "worker_engagement_id" not in attrs:
            raise serializers.ValidationError({"worker_engagement_id": "This field is required."})
        if not instance and "period_start" not in attrs:
            raise serializers.ValidationError({"period_start": "This field is required."})

        period_start = attrs.get("period_start", getattr(instance, "period_start", None))
        period_end = attrs.get("period_end", getattr(instance, "period_end", None))
        if period_start and period_end and period_end < period_start:
            raise serializers.ValidationError({"period_end": "Period end cannot be earlier than period start."})
        return attrs


class TimesheetDecisionSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(required=False, allow_blank=True)


class TimesheetEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TimesheetEvent
        fields = ["id", "action", "note", "metadata", "actor", "actor_name", "created_at"]

    def get_actor_name(self, obj):
        actor = obj.actor
        if not actor:
            return None
        return actor.get_full_name() or actor.email or actor.username


class TimesheetListSerializer(serializers.ModelSerializer):
    worker_name = serializers.SerializerMethodField(read_only=True)
    worker_email = serializers.EmailField(source="worker_profile.user.email", read_only=True)
    tenant_name = serializers.CharField(source="tenant.name", read_only=True)
    line_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Timesheet
        fields = [
            "id",
            "tenant",
            "tenant_name",
            "worker_profile",
            "worker_engagement",
            "worker_name",
            "worker_email",
            "engagement_type",
            "work_order_id",
            "work_order_number",
            "sow_id",
            "sow_number",
            "period_start",
            "period_end",
            "status",
            "total_hours",
            "regular_hours",
            "overtime_hours",
            "currency",
            "comment",
            "anomaly_reason",
            "qa_issues",
            "jurisdiction_flags",
            "assignment_snapshot",
            "submitted_at",
            "approved_at",
            "rejected_at",
            "rejection_reason",
            "line_count",
            "created_at",
            "updated_at",
        ]

    def get_worker_name(self, obj):
        user = obj.worker_profile.user
        return user.get_full_name() or obj.worker_profile.preferred_name or user.email

    def get_line_count(self, obj):
        if hasattr(obj, "_prefetched_objects_cache") and "lines" in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache["lines"])
        return obj.lines.count()


class TimesheetDetailSerializer(TimesheetListSerializer):
    lines = TimesheetLineSerializer(many=True, read_only=True)
    events = TimesheetEventSerializer(many=True, read_only=True)
    submitted_by_name = serializers.SerializerMethodField(read_only=True)
    approved_by_name = serializers.SerializerMethodField(read_only=True)
    rejected_by_name = serializers.SerializerMethodField(read_only=True)

    class Meta(TimesheetListSerializer.Meta):
        fields = [
            *TimesheetListSerializer.Meta.fields,
            "bill_rate",
            "pay_rate",
            "approval_brief",
            "submitted_by",
            "submitted_by_name",
            "approved_by",
            "approved_by_name",
            "rejected_by",
            "rejected_by_name",
            "lines",
            "events",
        ]

    def _actor_name(self, user):
        if not user:
            return None
        return user.get_full_name() or user.email or user.username

    def get_submitted_by_name(self, obj):
        return self._actor_name(obj.submitted_by)

    def get_approved_by_name(self, obj):
        return self._actor_name(obj.approved_by)

    def get_rejected_by_name(self, obj):
        return self._actor_name(obj.rejected_by)


class CostAllocationRequestSerializer(serializers.Serializer):
    worker_engagement_id = serializers.IntegerField(min_value=1)
    tasks = serializers.ListField(child=serializers.DictField(), allow_empty=False)

    def validate_tasks(self, value):
        normalized = []
        for item in value:
            task_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not task_id:
                raise serializers.ValidationError("Each task requires an id.")
            if not name:
                raise serializers.ValidationError("Each task requires a name.")
            normalized.append({"id": task_id, "name": name})
        return normalized
