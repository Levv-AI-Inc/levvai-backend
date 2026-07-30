from decimal import Decimal, ROUND_HALF_UP

from rest_framework import serializers

from apps.workorders.models import WorkOrder


class WorkOrderWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkOrder
        fields = [
            "intake",
            "selected_candidate",
            "supplier",
            "worker_full_name",
            "worker_email",
            "worker_phone",
            "role_definition",
            "start_date",
            "end_date",
            "bill_rate",
            "pay_rate",
            "currency",
            "hours_per_week",
            "overtime_enabled",
            "overtime_multiplier",
            "estimated_cost",
            "budget_amount",
            "cost_center",
            "legal_entity",
            "site",
            "work_location_label",
            "notes",
            "resume_url",
            "risk_flags",
        ]

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        currency = attrs.get("currency", getattr(instance, "currency", ""))
        if currency:
            currency = str(currency).strip().upper()
            if len(currency) != 3:
                raise serializers.ValidationError({"currency": "Currency must be a 3-letter ISO 4217 code."})
            attrs["currency"] = currency

        worker_full_name = attrs.get("worker_full_name")
        if worker_full_name is not None:
            attrs["worker_full_name"] = str(worker_full_name).strip()

        worker_phone = attrs.get("worker_phone")
        if worker_phone is not None:
            attrs["worker_phone"] = str(worker_phone).strip()

        work_location_label = attrs.get("work_location_label")
        if work_location_label is not None:
            attrs["work_location_label"] = str(work_location_label).strip()

        notes = attrs.get("notes")
        if notes is not None:
            attrs["notes"] = str(notes).strip()

        bill_rate = attrs.get("bill_rate", getattr(instance, "bill_rate", None))
        pay_rate = attrs.get("pay_rate", getattr(instance, "pay_rate", None))
        hours_per_week = attrs.get("hours_per_week", getattr(instance, "hours_per_week", None))
        overtime_enabled = attrs.get("overtime_enabled", getattr(instance, "overtime_enabled", False))
        overtime_multiplier = attrs.get("overtime_multiplier", getattr(instance, "overtime_multiplier", None))
        start_date = attrs.get("start_date", getattr(instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(instance, "end_date", None))

        if bill_rate is not None and bill_rate < 0:
            raise serializers.ValidationError({"bill_rate": "Bill rate must be greater than or equal to 0."})
        if pay_rate is not None and pay_rate < 0:
            raise serializers.ValidationError({"pay_rate": "Pay rate must be greater than or equal to 0."})
        if hours_per_week is not None and hours_per_week < 0:
            raise serializers.ValidationError({"hours_per_week": "Hours per week must be greater than or equal to 0."})
        if overtime_enabled and overtime_multiplier is None:
            raise serializers.ValidationError(
                {"overtime_multiplier": "Overtime multiplier is required when overtime is enabled."}
            )
        if overtime_multiplier is not None and overtime_multiplier <= 0:
            raise serializers.ValidationError(
                {"overtime_multiplier": "Overtime multiplier must be greater than 0."}
            )
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError({"end_date": "End date cannot be earlier than start date."})

        risk_flags = attrs.get("risk_flags", getattr(instance, "risk_flags", None))
        if risk_flags is not None:
            if not isinstance(risk_flags, list):
                raise serializers.ValidationError({"risk_flags": "Risk flags must be a list of strings."})
            normalized_flags = []
            for item in risk_flags:
                text = str(item).strip()
                if text:
                    normalized_flags.append(text)
            attrs["risk_flags"] = normalized_flags

        intake = attrs.get("intake", getattr(instance, "intake", None))
        selected_candidate = attrs.get("selected_candidate", getattr(instance, "selected_candidate", None))
        if intake and selected_candidate and selected_candidate.intake_id != intake.id:
            raise serializers.ValidationError(
                {"selected_candidate": "Selected candidate must belong to the selected intake."}
            )

        supplier = attrs.get("supplier", getattr(instance, "supplier", None))
        if supplier and selected_candidate and selected_candidate.supplier_id != supplier.id:
            raise serializers.ValidationError({"supplier": "Supplier must match the selected candidate supplier."})

        return attrs


class WorkOrderListSerializer(serializers.ModelSerializer):
    intake_title = serializers.CharField(source="intake.title", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    role_name = serializers.CharField(source="role_definition.name", read_only=True)
    current_approver_name = serializers.SerializerMethodField(read_only=True)
    approvals_remaining = serializers.SerializerMethodField(read_only=True)
    engagement_id = serializers.SerializerMethodField(read_only=True)
    engagement_number = serializers.SerializerMethodField(read_only=True)
    engagement_status = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "work_order_number",
            "status",
            "approval_status",
            "supplier_acceptance_status",
            "intake",
            "intake_title",
            "supplier",
            "supplier_name",
            "role_definition",
            "role_name",
            "worker_full_name",
            "currency",
            "bill_rate",
            "estimated_cost",
            "submitted_at",
            "created_at",
            "updated_at",
            "current_approver_name",
            "approvals_remaining",
            "engagement_id",
            "engagement_number",
            "engagement_status",
        ]

    def get_engagement_id(self, obj):
        engagement = _get_engagement(obj)
        return engagement.id if engagement else None

    def get_engagement_number(self, obj):
        engagement = _get_engagement(obj)
        return engagement.engagement_number if engagement else None

    def get_engagement_status(self, obj):
        engagement = _get_engagement(obj)
        return engagement.status if engagement else None

    def get_current_approver_name(self, obj):
        snapshot = obj.approval_chain_snapshot or {}
        resolved_steps = snapshot.get("resolved_steps") or []
        current_sequence = snapshot.get("current_step_sequence")
        current_step = None
        if current_sequence is not None:
            for step in resolved_steps:
                if step.get("sequence") == current_sequence:
                    current_step = step
                    break
        if current_step is None:
            for step in sorted(resolved_steps, key=lambda item: item.get("sequence") or 0):
                if step.get("status") not in {"approved", "rejected"}:
                    current_step = step
                    break
        return current_step.get("approver_name") if current_step else None

    def get_approvals_remaining(self, obj):
        snapshot = obj.approval_chain_snapshot or {}
        value = snapshot.get("approvals_remaining")
        if value is not None:
            return value
        resolved_steps = snapshot.get("resolved_steps") or []
        return sum(1 for step in resolved_steps if step.get("status") not in {"approved", "rejected"})


class WorkOrderDetailSerializer(serializers.ModelSerializer):
    intake_title = serializers.CharField(source="intake.title", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    role_name = serializers.CharField(source="role_definition.name", read_only=True)
    source_snapshot = serializers.SerializerMethodField(read_only=True)
    pricing = serializers.SerializerMethodField(read_only=True)
    markup_percent = serializers.SerializerMethodField(read_only=True)
    base_rate = serializers.SerializerMethodField(read_only=True)
    engagement_id = serializers.SerializerMethodField(read_only=True)
    engagement_number = serializers.SerializerMethodField(read_only=True)
    engagement_status = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "tenant_id",
            "work_order_number",
            "intake",
            "intake_title",
            "selected_candidate",
            "supplier",
            "supplier_name",
            "worker_full_name",
            "worker_email",
            "worker_phone",
            "role_definition",
            "role_name",
            "status",
            "approval_status",
            "supplier_acceptance_status",
            "supplier_response_notes",
            "supplier_accepted_at",
            "supplier_accepted_by",
            "supplier_change_requested_at",
            "supplier_change_requested_by",
            "start_date",
            "end_date",
            "bill_rate",
            "pay_rate",
            "currency",
            "hours_per_week",
            "overtime_enabled",
            "overtime_multiplier",
            "estimated_cost",
            "budget_amount",
            "cost_center",
            "legal_entity",
            "site",
            "work_location_label",
            "notes",
            "resume_url",
            "approval_chain",
            "approval_chain_snapshot",
            "source_snapshot",
            "pricing",
            "base_rate",
            "markup_percent",
            "risk_flags",
            "submitted_at",
            "submitted_by",
            "decision_at",
            "decided_by",
            "decision_reason",
            "created_by",
            "created_at",
            "updated_at",
            "engagement_id",
            "engagement_number",
            "engagement_status",
        ]

    def get_pricing(self, obj):
        source_snapshot = obj.source_snapshot or {}
        pricing = source_snapshot.get("pricing")
        if isinstance(pricing, dict):
            return _normalize_pricing_payload(pricing)
        return None

    def get_markup_percent(self, obj):
        pricing = self.get_pricing(obj) or {}
        return pricing.get("total_percent_markup")

    def get_base_rate(self, obj):
        pricing = self.get_pricing(obj) or {}
        return pricing.get("base_amount")

    def get_source_snapshot(self, obj):
        source_snapshot = obj.source_snapshot or {}
        if not isinstance(source_snapshot, dict):
            return source_snapshot

        payload = dict(source_snapshot)
        pricing = payload.get("pricing")
        if isinstance(pricing, dict):
            payload["pricing"] = _normalize_pricing_payload(pricing)
        return payload

    def get_engagement_id(self, obj):
        engagement = _get_engagement(obj)
        return engagement.id if engagement else None

    def get_engagement_number(self, obj):
        engagement = _get_engagement(obj)
        return engagement.engagement_number if engagement else None

    def get_engagement_status(self, obj):
        engagement = _get_engagement(obj)
        return engagement.status if engagement else None


def _format_decimal_2(value):
    if value in (None, ""):
        return None

    try:
        normalized = Decimal(str(value))
    except Exception:
        return value

    rounded = normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f")


def _normalize_pricing_payload(pricing):
    payload = dict(pricing)
    for key in [
        "bill_rate",
        "base_amount",
        "total_percent_markup",
        "total_fixed_markup",
    ]:
        payload[key] = _format_decimal_2(payload.get(key))

    components = payload.get("components")
    if isinstance(components, list):
        payload["components"] = [
            {
                **component,
                "numeric_value": _format_decimal_2(component.get("numeric_value")),
            }
            if isinstance(component, dict)
            else component
            for component in components
        ]

    breakdown = payload.get("breakdown")
    if isinstance(breakdown, list):
        payload["breakdown"] = [
            {
                **entry,
                "entered_value": _format_decimal_2(entry.get("entered_value")),
            }
            if isinstance(entry, dict)
            else entry
            for entry in breakdown
        ]

    return payload


class WorkOrderDecisionSerializer(serializers.Serializer):
    decision_reason = serializers.CharField(required=False, allow_blank=True, default="")


class WorkOrderSupplierDecisionSerializer(serializers.Serializer):
    supplier_response_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )


class WorkOrderSupplierChangeRequestSerializer(serializers.Serializer):
    supplier_response_notes = serializers.CharField(
        required=True,
        allow_blank=False,
    )


def _get_engagement(work_order):
    try:
        return work_order.engagement
    except Exception:
        return None
