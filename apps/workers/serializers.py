from datetime import date

from django.utils import timezone
from rest_framework import serializers

from apps.workers.models import (
    Engagement,
    LifecycleActivity,
    LifecycleBlock,
    LifecycleRun,
    Worker,
    WorkerEngagement,
)
from apps.workers.permissions import can_manage_worker, can_update_activity


def _source_value(work_order, *keys, default=""):
    snapshot = work_order.source_snapshot or {}
    containers = [
        snapshot.get("effective_values"),
        snapshot.get("intake"),
        (snapshot.get("intake") or {}).get("custom_fields")
        if isinstance(snapshot.get("intake"), dict)
        else None,
        snapshot,
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                return value
    return default


def _business_days_until(value):
    if not value:
        return None
    today = timezone.localdate()
    if value <= today:
        return 0
    cursor = today
    count = 0
    while cursor < value:
        cursor = date.fromordinal(cursor.toordinal() + 1)
        if cursor.weekday() < 5:
            count += 1
    return count


def _onboarding_run(engagement):
    worker_engagement = getattr(engagement, "worker_engagement", None)
    if not worker_engagement:
        return None
    return (
        worker_engagement.lifecycle_runs.filter(
            lifecycle_type=LifecycleRun.TYPE_ONBOARDING
        )
        .prefetch_related("blocks__activities")
        .first()
    )


class EngagementListSerializer(serializers.ModelSerializer):
    work_order_number = serializers.CharField(
        source="work_order.work_order_number",
        read_only=True,
    )
    work_order_status = serializers.CharField(
        source="work_order.status",
        read_only=True,
    )
    intake = serializers.IntegerField(
        source="work_order.intake_id",
        read_only=True,
        allow_null=True,
    )
    intake_title = serializers.CharField(
        source="work_order.intake.title",
        read_only=True,
        allow_null=True,
    )
    supplier = serializers.IntegerField(
        source="work_order.supplier_id",
        read_only=True,
        allow_null=True,
    )
    supplier_name = serializers.CharField(
        source="work_order.supplier.name",
        read_only=True,
        allow_null=True,
    )
    role_definition = serializers.IntegerField(
        source="work_order.role_definition_id",
        read_only=True,
        allow_null=True,
    )
    role_name = serializers.CharField(
        source="work_order.role_definition.name",
        read_only=True,
        allow_null=True,
    )
    worker_full_name = serializers.CharField(
        source="work_order.worker_full_name",
        read_only=True,
    )
    start_date = serializers.DateField(
        source="work_order.start_date",
        read_only=True,
        allow_null=True,
    )
    end_date = serializers.DateField(
        source="work_order.end_date",
        read_only=True,
        allow_null=True,
    )
    bill_rate = serializers.DecimalField(
        source="work_order.bill_rate",
        max_digits=12,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    currency = serializers.CharField(
        source="work_order.currency",
        read_only=True,
    )
    work_location_label = serializers.CharField(
        source="work_order.work_location_label",
        read_only=True,
    )

    class Meta:
        model = Engagement
        fields = [
            "id",
            "engagement_number",
            "work_order",
            "work_order_number",
            "work_order_status",
            "intake",
            "intake_title",
            "supplier",
            "supplier_name",
            "role_definition",
            "role_name",
            "worker_full_name",
            "status",
            "start_date",
            "end_date",
            "bill_rate",
            "currency",
            "work_location_label",
            "created_at",
            "accepted_at",
            "change_requested_at",
        ]
        read_only_fields = fields


class EngagementDetailSerializer(EngagementListSerializer):
    work_order_approval_status = serializers.CharField(
        source="work_order.approval_status",
        read_only=True,
    )
    worker_email = serializers.EmailField(
        source="work_order.worker_email",
        read_only=True,
    )
    pay_rate = serializers.DecimalField(
        source="work_order.pay_rate",
        max_digits=12,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    hours_per_week = serializers.DecimalField(
        source="work_order.hours_per_week",
        max_digits=5,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    overtime_enabled = serializers.BooleanField(
        source="work_order.overtime_enabled",
        read_only=True,
    )
    overtime_multiplier = serializers.DecimalField(
        source="work_order.overtime_multiplier",
        max_digits=5,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    estimated_cost = serializers.DecimalField(
        source="work_order.estimated_cost",
        max_digits=14,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    overtime_rules_label = serializers.SerializerMethodField()
    supervisor_name = serializers.SerializerMethodField()
    onboarding_tasks = serializers.SerializerMethodField()
    required_documents = serializers.SerializerMethodField()
    invoice_cycle = serializers.SerializerMethodField()
    payment_terms_label = serializers.SerializerMethodField()

    class Meta(EngagementListSerializer.Meta):
        fields = EngagementListSerializer.Meta.fields + [
            "tenant_id",
            "work_order_approval_status",
            "worker_email",
            "pay_rate",
            "hours_per_week",
            "overtime_enabled",
            "overtime_multiplier",
            "overtime_rules_label",
            "estimated_cost",
            "supervisor_name",
            "onboarding_tasks",
            "required_documents",
            "invoice_cycle",
            "payment_terms_label",
            "supplier_response_notes",
            "source_snapshot",
            "accepted_by",
            "change_requested_by",
            "created_by",
            "updated_at",
        ]
        read_only_fields = fields

    def get_overtime_rules_label(self, obj):
        work_order = obj.work_order
        if not work_order.overtime_enabled:
            return "No overtime configured"
        multiplier = work_order.overtime_multiplier or "1.0"
        return f"{multiplier}x overtime enabled"

    def get_supervisor_name(self, obj):
        return str(
            _source_value(
                obj.work_order,
                "supervisor_name",
                "hiring_manager_name",
                "manager_name",
                "manager",
            )
            or ""
        )

    def get_onboarding_tasks(self, obj):
        run = _onboarding_run(obj)
        if not run:
            return []
        return [
            activity.name
            for block in run.blocks.all()
            for activity in block.activities.all()
        ]

    def get_required_documents(self, obj):
        run = _onboarding_run(obj)
        if not run:
            return []
        return [
            activity.name
            for block in run.blocks.all()
            for activity in block.activities.all()
            if activity.owner == "worker"
        ]

    def get_invoice_cycle(self, obj):
        return str(_source_value(obj.work_order, "invoice_cycle") or "")

    def get_payment_terms_label(self, obj):
        return str(
            _source_value(
                obj.work_order,
                "payment_terms_label",
                "payment_terms",
            )
            or ""
        )


class EngagementDecisionSerializer(serializers.Serializer):
    supplier_response_notes = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )


class EngagementRequestChangeSerializer(serializers.Serializer):
    supplier_response_notes = serializers.CharField(
        required=True,
        allow_blank=False,
    )


class LifecycleActivityUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=LifecycleActivity.STATUS_CHOICES,
    )
    evidence = serializers.JSONField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


class WorkerRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    worker_invite_token = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=128,
    )


class WorkerContractExtensionSerializer(serializers.Serializer):
    work_order_id = serializers.IntegerField(
        min_value=1,
        required=False,
    )
    engagement_id = serializers.IntegerField(
        min_value=1,
        required=False,
    )
    end_date = serializers.DateField()
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
    )

    def validate(self, attrs):
        if not attrs.get("work_order_id") and not attrs.get("engagement_id"):
            raise serializers.ValidationError(
                {"work_order_id": "Work order is required."}
            )
        return attrs


def readiness_for_run(run):
    activities = [
        activity
        for block in run.blocks.all()
        for activity in block.activities.all()
    ]
    if not activities:
        return 0
    completed = sum(
        1
        for activity in activities
        if activity.status
        in {
            LifecycleActivity.STATUS_COMPLETE,
            LifecycleActivity.STATUS_WAIVED,
        }
    )
    return round(completed * 100 / len(activities))


def _current_blocker(run):
    blocks = list(run.blocks.all())
    block = next(
        (
            item
            for item in blocks
            if item.status == LifecycleBlock.STATUS_BLOCKED
        ),
        None,
    )
    if block is None:
        block = next(
            (
                item
                for item in blocks
                if item.status == LifecycleBlock.STATUS_IN_PROGRESS
                and item.gate_type == "hard"
            ),
            None,
        )
    if block is None:
        block = next(
            (
                item
                for item in blocks
                if item.status == LifecycleBlock.STATUS_IN_PROGRESS
            ),
            None,
        )
    if block is None:
        return None, None
    activity = next(
        (
            item
            for item in block.activities.all()
            if item.status
            not in {
                LifecycleActivity.STATUS_COMPLETE,
                LifecycleActivity.STATUS_WAIVED,
            }
        ),
        None,
    )
    return block, activity


def _pending_with(block, activity):
    config_owner = (block.config or {}).get("accountable_owner") if block else ""
    if config_owner:
        return str(config_owner).upper()
    owner = getattr(activity, "owner", "")
    return {
        "worker": "WORKER",
        "supplier": "SUPPLIER",
        "hiring_manager": "HIRING MANAGER",
        "it": "IT",
        "system": "SYSTEM",
    }.get(owner, "TEAM")


def _manager_name(work_order):
    value = _source_value(
        work_order,
        "supervisor_name",
        "hiring_manager_name",
        "manager_name",
        "manager",
    )
    if value:
        return str(value)
    creator = work_order.created_by
    if creator:
        return creator.get_full_name().strip() or creator.email or creator.username
    return ""


def _department_name(work_order):
    cost_center = work_order.cost_center
    business_unit = getattr(cost_center, "business_unit", None)
    if business_unit:
        return business_unit.name
    if cost_center:
        return cost_center.name
    return ""


def _visible_worker_assignments(worker):
    assignments = getattr(worker, "visible_assignments", None)
    if assignments is not None:
        return list(assignments)
    return list(
        worker.engagements.select_related(
            "work_order__intake",
            "work_order__supplier",
            "work_order__role_definition",
            "work_order__cost_center__business_unit",
            "work_order__site",
            "work_order__created_by",
            "engagement__work_order__intake",
            "engagement__work_order__supplier",
            "engagement__work_order__role_definition",
            "engagement__work_order__cost_center__business_unit",
            "engagement__work_order__site",
            "engagement__work_order__created_by",
        ).prefetch_related("lifecycle_runs__blocks__activities")
    )


def _assignment_work_order(assignment):
    return assignment.resolved_work_order


def _assignment_sort_key(assignment):
    priority = {
        WorkerEngagement.STATUS_ACTIVE: 0,
        WorkerEngagement.STATUS_ONBOARDING: 1,
        WorkerEngagement.STATUS_OFFBOARDING: 2,
        WorkerEngagement.STATUS_COMPLETE: 3,
        WorkerEngagement.STATUS_CANCELLED: 4,
    }
    work_order = _assignment_work_order(assignment)
    start_date = work_order.start_date if work_order else None
    return (
        priority.get(assignment.status, 99),
        -(start_date.toordinal() if start_date else 0),
        -assignment.id,
    )


def _current_worker_assignment(
    worker,
    engagement_id=None,
    work_order_id=None,
):
    assignments = _visible_worker_assignments(worker)
    if work_order_id is not None:
        return next(
            (
                assignment
                for assignment in assignments
                if assignment.work_order_id == work_order_id
                or (
                    assignment.engagement_id
                    and assignment.engagement.work_order_id == work_order_id
                )
            ),
            None,
        )
    if engagement_id is not None:
        return next(
            (
                assignment
                for assignment in assignments
                if assignment.engagement_id == engagement_id
            ),
            None,
        )
    return min(assignments, key=_assignment_sort_key) if assignments else None


def _assignment_run(assignment, lifecycle_type):
    if assignment is None:
        return None
    runs = list(assignment.lifecycle_runs.all())
    return next(
        (
            run
            for run in runs
            if run.lifecycle_type == lifecycle_type
        ),
        None,
    )


def _worker_compliance(worker):
    if worker.status == Worker.STATUS_OFFBOARDED:
        return "compliant"

    runs = [
        run
        for assignment in _visible_worker_assignments(worker)
        for run in assignment.lifecycle_runs.all()
    ]
    activities = [
        activity
        for run in runs
        for block in run.blocks.all()
        for activity in block.activities.all()
    ]
    if any(
        activity.status == LifecycleActivity.STATUS_BLOCKED
        for activity in activities
    ) or any(run.status == LifecycleRun.STATUS_BLOCKED for run in runs):
        return "non_compliant"
    if worker.status in {
        Worker.STATUS_INVITED,
        Worker.STATUS_ONBOARDING,
        Worker.STATUS_OFFBOARDING,
    }:
        return "review_required"
    if any(
        run.status
        not in {
            LifecycleRun.STATUS_COMPLETE,
            LifecycleRun.STATUS_CANCELLED,
        }
        for run in runs
    ):
        return "review_required"
    return "compliant"


def _worker_type_label(assignment):
    if assignment is None:
        return ""
    work_order = _assignment_work_order(assignment)
    intake = work_order.intake if work_order else None
    if intake and intake.engagement_type == "sow":
        return "SOW"
    return assignment.get_worker_type_display()


def _mapping_value(mapping, keys):
    if not isinstance(mapping, dict):
        return ""
    containers = [
        mapping,
        mapping.get("effective_values"),
        mapping.get("custom_fields"),
        mapping.get("response"),
        mapping.get("result"),
        mapping.get("worker"),
        mapping.get("worker_runtime"),
    ]
    intake = mapping.get("intake")
    if isinstance(intake, dict):
        containers.extend([intake, intake.get("custom_fields")])
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _external_worker_id(assignment):
    if assignment is None:
        return ""
    work_order = _assignment_work_order(assignment)
    if work_order is None:
        return ""
    keys = (
        "hr_system_id",
        "hr_id",
        "workday_id",
        "external_worker_id",
        "employee_id",
    )
    sources = [work_order.source_snapshot]
    if assignment.engagement_id:
        sources.append(assignment.engagement.source_snapshot)
    for source in sources:
        value = _mapping_value(source, keys)
        if value:
            return value
    for run in assignment.lifecycle_runs.all():
        for block in run.blocks.all():
            for activity in block.activities.all():
                value = _mapping_value(activity.evidence, keys)
                if value:
                    return value
    return ""


def _worker_assignment_payload(assignment, current_assignment):
    work_order = _assignment_work_order(assignment)
    onboarding = _assignment_run(
        assignment,
        LifecycleRun.TYPE_ONBOARDING,
    )
    offboarding = _assignment_run(
        assignment,
        LifecycleRun.TYPE_OFFBOARDING,
    )
    return {
        "worker_engagement_id": assignment.id,
        "engagement_id": assignment.engagement_id,
        "engagement_number": (
            assignment.engagement.engagement_number
            if assignment.engagement_id
            else ""
        ),
        "work_order_id": work_order.id,
        "work_order_number": work_order.work_order_number,
        "worker_type": assignment.worker_type,
        "worker_type_label": _worker_type_label(assignment),
        "status": assignment.status,
        "supplier": work_order.supplier.name if work_order.supplier else "",
        "role": (
            work_order.role_definition.name
            if work_order.role_definition
            else ""
        ),
        "start_date": work_order.start_date,
        "end_date": work_order.end_date,
        "location": work_order.work_location_label,
        "onboarding_run_id": onboarding.id if onboarding else None,
        "onboarding_status": onboarding.status if onboarding else "",
        "offboarding_run_id": offboarding.id if offboarding else None,
        "offboarding_status": offboarding.status if offboarding else "",
        "is_current": bool(
            current_assignment
            and current_assignment.id == assignment.id
        ),
    }


def worker_directory_record(
    worker,
    *,
    engagement_id=None,
    work_order_id=None,
    include_assignments=False,
    request=None,
):
    assignments = sorted(
        _visible_worker_assignments(worker),
        key=_assignment_sort_key,
    )
    assignment = _current_worker_assignment(
        worker,
        engagement_id=engagement_id,
        work_order_id=work_order_id,
    )
    work_order = (
        _assignment_work_order(assignment)
        if assignment is not None
        else None
    )
    onboarding = _assignment_run(
        assignment,
        LifecycleRun.TYPE_ONBOARDING,
    )
    offboarding = _assignment_run(
        assignment,
        LifecycleRun.TYPE_OFFBOARDING,
    )
    cws_id = (
        _source_value(
            work_order,
            "cws_id",
            "contingent_worker_id",
        )
        if work_order
        else ""
    ) or f"CWS-{worker.id:06d}"
    can_manage = can_manage_worker(request) if request else False
    payload = {
        "worker_id": worker.id,
        "cws_id": str(cws_id),
        "hr_system_id": _external_worker_id(assignment),
        "name": worker.full_name,
        "email": worker.email,
        "phone": worker.phone,
        "worker_status": worker.status,
        "worker_type": assignment.worker_type if assignment else "",
        "worker_type_label": _worker_type_label(assignment),
        "compliance_status": _worker_compliance(worker),
        "registered_at": worker.registered_at,
        "worker_engagement_id": assignment.id if assignment else None,
        "engagement_id": (
            assignment.engagement_id if assignment else None
        ),
        "engagement_number": (
            assignment.engagement.engagement_number
            if assignment and assignment.engagement_id
            else ""
        ),
        "work_order_id": work_order.id if work_order else None,
        "work_order_number": (
            work_order.work_order_number if work_order else ""
        ),
        "assignment_status": assignment.status if assignment else "",
        "supplier": (
            work_order.supplier.name
            if work_order and work_order.supplier
            else ""
        ),
        "role": (
            work_order.role_definition.name
            if work_order and work_order.role_definition
            else ""
        ),
        "owner": _manager_name(work_order) if work_order else "",
        "department": (
            _department_name(work_order) if work_order else ""
        ),
        "location": (
            work_order.work_location_label if work_order else ""
        ),
        "start_date": work_order.start_date if work_order else None,
        "end_date": work_order.end_date if work_order else None,
        "onboarding_run_id": onboarding.id if onboarding else None,
        "onboarding_status": onboarding.status if onboarding else "",
        "offboarding_run_id": offboarding.id if offboarding else None,
        "offboarding_status": offboarding.status if offboarding else "",
        "permissions": {
            "can_view_profile": True,
            "can_extend_contract": bool(
                can_manage
                and assignment
                and assignment.status
                in {
                    WorkerEngagement.STATUS_ONBOARDING,
                    WorkerEngagement.STATUS_ACTIVE,
                }
            ),
            "can_offboard": bool(
                can_manage
                and assignment
                and assignment.status
                in {
                    WorkerEngagement.STATUS_ONBOARDING,
                    WorkerEngagement.STATUS_ACTIVE,
                    WorkerEngagement.STATUS_OFFBOARDING,
                }
            ),
        },
    }
    if include_assignments:
        payload["assignments"] = [
            _worker_assignment_payload(item, assignment)
            for item in assignments
        ]
    return payload


def lifecycle_summary(run):
    assignment = run.worker_engagement
    worker = assignment.worker
    work_order = _assignment_work_order(assignment)
    block, activity = _current_blocker(run)
    invite = worker.invites.order_by("-created_at").first()
    status_label = {
        LifecycleRun.STATUS_COMPLETE: "Ready",
        LifecycleRun.STATUS_BLOCKED: "Blocked",
        LifecycleRun.STATUS_IN_PROGRESS: "In Progress",
        LifecycleRun.STATUS_PENDING: "Pending",
        LifecycleRun.STATUS_CANCELLED: "Cancelled",
    }.get(run.status, run.status)

    return {
        "run_id": run.id,
        "worker_id": worker.id,
        "worker_engagement_id": assignment.id,
        "engagement_id": assignment.engagement_id,
        "engagement_number": (
            assignment.engagement.engagement_number
            if assignment.engagement_id
            else ""
        ),
        "work_order_id": work_order.id,
        "work_order_number": work_order.work_order_number,
        "lifecycle_type": run.lifecycle_type,
        "name": worker.full_name,
        "email": worker.email,
        "role": work_order.role_definition.name
        if work_order.role_definition
        else "",
        "supplier": work_order.supplier.name if work_order.supplier else "",
        "start_date": work_order.start_date,
        "end_date": work_order.end_date,
        "readiness": readiness_for_run(run),
        "status": status_label,
        "run_status": run.status,
        "pending_with": _pending_with(block, activity),
        "current_blocker_task": (
            activity.name if activity else block.name if block else ""
        ),
        "current_blocker_block": block.name if block else "",
        "manager": _manager_name(work_order),
        "department": _department_name(work_order),
        "cost_center": work_order.cost_center.code
        if work_order.cost_center
        else "",
        "worker_status": worker.status,
        "registration_status": (
            "registered"
            if worker.user_id
            else invite.status
            if invite
            else "not_invited"
        ),
        "registered_at": worker.registered_at,
        "business_days_until_start": _business_days_until(
            work_order.start_date
        ),
        "active_gate_blocker": run.status == LifecycleRun.STATUS_BLOCKED,
        "workflow_id": run.workflow_id,
        "workflow_name": run.workflow_name,
        "workflow_version": run.workflow_version,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "updated_at": run.updated_at,
    }


def lifecycle_detail(run, request=None):
    summary = lifecycle_summary(run)
    blocks = []
    governance_log = []
    for block in run.blocks.all():
        activities = []
        for activity in block.activities.all():
            can_update = (
                can_update_activity(request, activity) if request else False
            )
            activity_payload = {
                "id": activity.id,
                "sequence": activity.sequence,
                "name": activity.name,
                "owner": activity.owner,
                "status": activity.status,
                "config": activity.config,
                "evidence": activity.evidence,
                "notes": activity.notes,
                "can_update": can_update,
                "started_at": activity.started_at,
                "completed_at": activity.completed_at,
                "completed_by": activity.completed_by_id,
                "updated_at": activity.updated_at,
            }
            activities.append(activity_payload)
            if activity.status in {
                LifecycleActivity.STATUS_COMPLETE,
                LifecycleActivity.STATUS_WAIVED,
            }:
                governance_log.append(
                    {
                        "activity_id": activity.id,
                        "name": activity.name,
                        "status": activity.status,
                        "owner": activity.owner,
                        "completed_at": activity.completed_at,
                        "evidence": activity.evidence,
                    }
                )
        blocks.append(
            {
                "id": block.id,
                "source_block_id": block.source_block_id,
                "client_key": block.client_key,
                "sequence": block.sequence,
                "block_type": block.block_type,
                "name": block.name,
                "gate_type": block.gate_type,
                "integration_type": block.integration_type,
                "status": block.status,
                "config": block.config,
                "layout": block.layout,
                "activities": activities,
                "started_at": block.started_at,
                "completed_at": block.completed_at,
            }
        )

    assignment = run.worker_engagement
    worker = assignment.worker
    work_order = _assignment_work_order(assignment)
    can_manage = can_manage_worker(request) if request else False
    return {
        **summary,
        "tenant_id": run.tenant_id,
        "permissions": {
            "can_manage_worker": can_manage,
            "can_start_offboarding": can_manage,
            "can_send_invite": can_manage and not worker.user_id,
        },
        "workflow": {
            "id": run.workflow_id,
            "name": run.workflow_name,
            "version": run.workflow_version,
            "derived": bool((run.snapshot or {}).get("derived")),
        },
        "graph": {
            "dependencies": (run.snapshot or {}).get("dependencies") or [],
        },
        "blocks": blocks,
        "governance_log": sorted(
            governance_log,
            key=lambda item: (
                item["completed_at"].timestamp()
                if item.get("completed_at")
                else float("-inf")
            ),
            reverse=True,
        ),
        "orchestration_pulse": _orchestration_pulse(run),
        "work_order": {
            "id": work_order.id,
            "number": work_order.work_order_number,
            "status": work_order.status,
            "location": work_order.work_location_label,
        },
    }


def _orchestration_pulse(run):
    block, activity = _current_blocker(run)
    if run.status == LifecycleRun.STATUS_COMPLETE:
        return "All configured lifecycle gates are complete."
    if block is None:
        return "The lifecycle run is waiting for its first eligible block."
    if block.status == LifecycleBlock.STATUS_BLOCKED:
        return (
            f"{block.name} is blocked by {activity.name if activity else 'an activity'}. "
            "Downstream hard-gated work remains locked."
        )
    if block.gate_type == "hard":
        return (
            f"{block.name} is the active hard gate. "
            "Downstream dependent blocks unlock when it completes."
        )
    return (
        f"{block.name} is in progress as a soft gate. "
        "Independent workflow branches can continue."
    )
