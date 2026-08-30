from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_tenants.utils import schema_context

from apps.accounts.models import Membership, WorkerEngagement
from apps.accounts.profile import active_worker_engagements_for_profile
from apps.timesheets.models import Timesheet, TimesheetEvent, TimesheetLine


class TimesheetValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__(str(errors))


class TimesheetTransitionError(Exception):
    pass


class TimesheetPermissionError(Exception):
    pass


APPROVER_ROLES = {
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_FINANCE,
}


class TimesheetService:
    @classmethod
    def create_for_worker(cls, *, worker_profile, user, attrs):
        engagement = cls._get_active_worker_engagement(worker_profile, attrs.get("worker_engagement_id"))
        period_start = attrs["period_start"]
        period_end = attrs.get("period_end") or period_start + timedelta(days=6)
        lines = attrs.get("lines") or []

        with transaction.atomic():
            timesheet = Timesheet(
                tenant=engagement.tenant,
                worker_profile=worker_profile,
                worker_engagement=engagement,
                engagement_type=engagement.engagement_type,
                work_order_id=engagement.work_order_id,
                work_order_number=engagement.work_order_number,
                sow_id=engagement.sow_id,
                sow_number=engagement.sow_number,
                period_start=period_start,
                period_end=period_end,
                status=Timesheet.STATUS_DRAFT,
                currency=attrs.get("currency") or "",
                comment=attrs.get("comment") or "",
                anomaly_reason=attrs.get("anomaly_reason") or "",
                qa_issues=attrs.get("qa_issues") or [],
                jurisdiction_flags=attrs.get("jurisdiction_flags") or [],
                approval_brief=attrs.get("approval_brief") or {},
                assignment_snapshot=cls.build_assignment_snapshot(engagement),
            )
            cls._apply_assignment_defaults(timesheet, engagement)
            cls._replace_lines(timesheet, lines, persist_parent=False)
            cls._clean_or_raise(timesheet)
            try:
                timesheet.save()
            except IntegrityError as exc:
                raise TimesheetValidationError(
                    {"period": "A non-voided timesheet already exists for this engagement and period."}
                ) from exc
            cls._replace_lines(timesheet, lines)
            cls._record_event(timesheet, TimesheetEvent.ACTION_CREATED, user)
        return timesheet

    @classmethod
    def update_worker_draft(cls, *, timesheet, worker_profile, user, attrs):
        cls.assert_worker_owns_timesheet(timesheet, worker_profile)
        if timesheet.status != Timesheet.STATUS_DRAFT:
            raise TimesheetTransitionError("Only draft timesheets can be edited.")

        lines = attrs.pop("lines", None)
        for field in [
            "period_start",
            "period_end",
            "currency",
            "comment",
            "anomaly_reason",
            "qa_issues",
            "jurisdiction_flags",
            "approval_brief",
        ]:
            if field in attrs:
                setattr(timesheet, field, attrs[field])
        if lines is not None:
            cls._replace_lines(timesheet, lines, persist_parent=False)
        cls._clean_or_raise(timesheet)
        try:
            timesheet.save()
        except IntegrityError as exc:
            raise TimesheetValidationError(
                {"period": "A non-voided timesheet already exists for this engagement and period."}
            ) from exc
        if lines is not None:
            cls._replace_lines(timesheet, lines)
        cls._record_event(timesheet, TimesheetEvent.ACTION_UPDATED, user)
        return timesheet

    @classmethod
    def submit_for_worker(cls, *, timesheet, worker_profile, user, attrs=None):
        cls.assert_worker_owns_timesheet(timesheet, worker_profile)
        if timesheet.status != Timesheet.STATUS_DRAFT:
            raise TimesheetTransitionError("Only draft timesheets can be submitted.")
        attrs = attrs or {}
        lines = attrs.pop("lines", None)

        with transaction.atomic():
            if lines is not None:
                cls._replace_lines(timesheet, lines, persist_parent=False)
            for field in ["comment", "anomaly_reason", "qa_issues", "jurisdiction_flags", "approval_brief"]:
                if field in attrs:
                    setattr(timesheet, field, attrs[field])
            timesheet.assignment_snapshot = cls.build_assignment_snapshot(timesheet.worker_engagement)
            cls._clean_or_raise(timesheet)
            if lines is not None:
                timesheet.save()
                cls._replace_lines(timesheet, lines)
            if not timesheet.lines.exists():
                raise TimesheetValidationError({"lines": "At least one timesheet line is required."})
            timesheet.status = Timesheet.STATUS_SUBMITTED
            timesheet.submitted_at = timezone.now()
            timesheet.submitted_by = user
            cls.recalculate_totals(timesheet)
            timesheet.full_clean()
            timesheet.save()
            cls._record_event(timesheet, TimesheetEvent.ACTION_SUBMITTED, user)
        return timesheet

    @classmethod
    def approve(cls, *, timesheet, user, membership):
        cls.assert_tenant_can_decide(timesheet, membership)
        if timesheet.status != Timesheet.STATUS_SUBMITTED:
            raise TimesheetTransitionError("Only submitted timesheets can be approved.")
        timesheet.status = Timesheet.STATUS_APPROVED
        timesheet.approved_at = timezone.now()
        timesheet.approved_by = user
        timesheet.rejected_at = None
        timesheet.rejected_by = None
        timesheet.rejection_reason = ""
        timesheet.full_clean()
        timesheet.save()
        cls._record_event(timesheet, TimesheetEvent.ACTION_APPROVED, user)
        return timesheet

    @classmethod
    def reject(cls, *, timesheet, user, membership, rejection_reason):
        cls.assert_tenant_can_decide(timesheet, membership)
        if timesheet.status != Timesheet.STATUS_SUBMITTED:
            raise TimesheetTransitionError("Only submitted timesheets can be rejected.")
        timesheet.status = Timesheet.STATUS_REJECTED
        timesheet.rejected_at = timezone.now()
        timesheet.rejected_by = user
        timesheet.rejection_reason = rejection_reason or ""
        timesheet.full_clean()
        timesheet.save()
        cls._record_event(timesheet, TimesheetEvent.ACTION_REJECTED, user, note=timesheet.rejection_reason)
        return timesheet

    @classmethod
    def recalculate_totals(cls, timesheet):
        total = Decimal("0")
        for line in timesheet.lines.all():
            total += _decimal(line.hours)
        timesheet.total_hours = _money(total)
        timesheet.regular_hours = _money(min(total, Decimal("40.00")))
        timesheet.overtime_hours = _money(max(total - Decimal("40.00"), Decimal("0.00")))
        return timesheet

    @classmethod
    def worker_queryset(cls, worker_profile):
        return (
            Timesheet.objects.filter(worker_profile=worker_profile)
            .select_related("tenant", "worker_profile__user", "worker_engagement")
            .prefetch_related("lines")
        )

    @classmethod
    def tenant_queryset(cls, request):
        queryset = (
            Timesheet.objects.filter(tenant_id=request.tenant.id)
            .select_related("tenant", "worker_profile__user", "worker_engagement", "submitted_by", "approved_by", "rejected_by")
            .prefetch_related("lines")
        )
        membership = getattr(request, "membership", None)
        if not membership:
            membership = Membership.objects.filter(
                user=request.user,
                tenant_id=request.tenant.id,
                status=Membership.STATUS_ACTIVE,
                is_active=True,
            ).first()
        if membership and membership.role == Membership.ROLE_SUPPLIER:
            queryset = queryset.filter(worker_engagement__supplier_id=membership.supplier_id)
        return queryset

    @classmethod
    def worker_context(cls, request, worker_profile):
        from apps.accounts.profile import build_worker_session_metadata

        worker_session = build_worker_session_metadata(request, worker_profile)
        timesheets = list(cls.worker_queryset(worker_profile).order_by("-period_start", "-id")[:20])
        return {
            "profile": {
                "type": "worker",
                "default_home": "/external/act-as-worker/timesheet",
            },
            "worker": worker_session,
            "assignments": [
                cls.serialize_assignment(engagement)
                for engagement in active_worker_engagements_for_profile(worker_profile)
            ],
            "timesheets": timesheets,
            "available_weeks": cls.available_weeks(),
        }

    @classmethod
    def available_weeks(cls, count=8):
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        weeks = []
        for offset in range(count):
            start = monday - timedelta(days=offset * 7)
            end = start + timedelta(days=6)
            weeks.append(
                {
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "label": f"{start.isoformat()} - {end.isoformat()}",
                    "is_current": offset == 0,
                }
            )
        return weeks

    @classmethod
    def build_assignment_snapshot(cls, engagement):
        snapshot = {
            "worker_engagement_id": engagement.id,
            "tenant_id": engagement.tenant_id,
            "tenant_name": engagement.tenant.name if engagement.tenant else engagement.client_name,
            "client_name": engagement.client_name or (engagement.tenant.name if engagement.tenant else ""),
            "engagement_type": engagement.engagement_type,
            "work_order_id": engagement.work_order_id,
            "work_order_number": engagement.work_order_number,
            "sow_id": engagement.sow_id,
            "sow_number": engagement.sow_number,
            "supplier_id": engagement.supplier_id,
            "supplier_name": engagement.supplier_name,
            "role_name": engagement.role_name,
            "start_date": engagement.start_date.isoformat() if engagement.start_date else None,
            "end_date": engagement.end_date.isoformat() if engagement.end_date else None,
            "expected_hours": 40,
            "jurisdiction": "",
            "cost_allocations": [],
        }
        work_order = cls._load_tenant_work_order(engagement)
        if work_order:
            snapshot.update(
                {
                    "work_order_id": work_order.id,
                    "work_order_number": work_order.work_order_number or engagement.work_order_number,
                    "worker_full_name": work_order.worker_full_name,
                    "currency": work_order.currency,
                    "bill_rate": _format_decimal(work_order.bill_rate),
                    "pay_rate": _format_decimal(work_order.pay_rate),
                    "expected_hours": _format_decimal(work_order.hours_per_week) or 40,
                    "jurisdiction": _jurisdiction_for_work_order(work_order),
                    "role_name": work_order.role_definition.name if work_order.role_definition else engagement.role_name,
                    "cost_allocations": cls.allowed_cost_allocations(engagement, work_order=work_order),
                }
            )
        elif engagement.work_order_number or engagement.sow_number:
            snapshot["cost_allocations"] = cls.allowed_cost_allocations(engagement)
        return snapshot

    @classmethod
    def serialize_assignment(cls, engagement):
        snapshot = cls.build_assignment_snapshot(engagement)
        reference = engagement.work_order_number or engagement.sow_number or str(engagement.id)
        label = snapshot.get("role_name") or reference
        return {
            "id": reference,
            "worker_engagement_id": engagement.id,
            "label": label,
            "type": "SOW" if engagement.engagement_type == WorkerEngagement.TYPE_SOW else "Work Order",
            "expected_hours": snapshot.get("expected_hours") or 40,
            "jurisdiction": snapshot.get("jurisdiction") or "",
            "cost_allocations": snapshot.get("cost_allocations") or [],
            "assignment_snapshot": snapshot,
        }

    @classmethod
    def allowed_cost_allocations(cls, engagement, work_order=None):
        work_order = work_order or cls._load_tenant_work_order(engagement)
        if work_order and work_order.cost_center:
            cost_center = work_order.cost_center
            return [
                {
                    "cost_center_id": cost_center.id,
                    "costCenter": cost_center.code,
                    "cost_center": cost_center.code,
                    "cost_center_code": cost_center.code,
                    "cost_center_name": cost_center.name,
                    "taskCode": "Hours Worked",
                    "task_code": "Hours Worked",
                    "label": cost_center.name,
                }
            ]
        fallback = engagement.work_order_number or engagement.sow_number or f"ENG-{engagement.id}"
        return [
            {
                "cost_center_id": None,
                "costCenter": fallback,
                "cost_center": fallback,
                "cost_center_code": fallback,
                "cost_center_name": engagement.client_name or (engagement.tenant.name if engagement.tenant else ""),
                "taskCode": "Hours Worked",
                "task_code": "Hours Worked",
                "label": engagement.role_name or "Hours worked",
            }
        ]

    @classmethod
    def assign_cost_allocations(cls, *, worker_profile, engagement_id, tasks):
        engagement = cls._get_active_worker_engagement(worker_profile, engagement_id)
        allocations = cls.allowed_cost_allocations(engagement)
        if not allocations:
            raise TimesheetValidationError({"cost_allocations": "No cost allocations configured on this assignment."})
        only = allocations[0]
        return [
            {
                "taskId": task["id"],
                "costCenter": only["costCenter"],
                "taskCode": only["taskCode"],
                "cost_center_id": only["cost_center_id"],
                "cost_center_code": only["cost_center_code"],
                "cost_center_name": only["cost_center_name"],
                "rationale": "Only one cost allocation is configured on this assignment.",
            }
            for task in tasks
        ]

    @classmethod
    def assert_worker_owns_timesheet(cls, timesheet, worker_profile):
        if timesheet.worker_profile_id != worker_profile.id:
            raise TimesheetPermissionError("Timesheet does not belong to this worker.")

    @classmethod
    def assert_tenant_can_decide(cls, timesheet, membership):
        if not membership or membership.role not in APPROVER_ROLES:
            raise TimesheetPermissionError("You do not have permission to decide this timesheet.")
        if membership.role == Membership.ROLE_SUPPLIER:
            raise TimesheetPermissionError("Supplier users cannot approve timesheets.")

    @classmethod
    def _get_active_worker_engagement(cls, worker_profile, engagement_id):
        engagement = (
            WorkerEngagement.objects.filter(
                id=engagement_id,
                worker_profile=worker_profile,
                status=WorkerEngagement.STATUS_ACTIVE,
            )
            .select_related("tenant")
            .first()
        )
        if not engagement:
            raise TimesheetValidationError({"worker_engagement_id": "Active worker engagement was not found."})
        return engagement

    @classmethod
    def _apply_assignment_defaults(cls, timesheet, engagement):
        snapshot = timesheet.assignment_snapshot or {}
        timesheet.currency = timesheet.currency or snapshot.get("currency") or "USD"
        if timesheet.bill_rate is None and snapshot.get("bill_rate") is not None:
            timesheet.bill_rate = Decimal(str(snapshot["bill_rate"]))
        if timesheet.pay_rate is None and snapshot.get("pay_rate") is not None:
            timesheet.pay_rate = Decimal(str(snapshot["pay_rate"]))
        if not timesheet.work_order_number:
            timesheet.work_order_number = engagement.work_order_number
        if not timesheet.sow_number:
            timesheet.sow_number = engagement.sow_number

    @classmethod
    def _replace_lines(cls, timesheet, lines, persist_parent=True):
        allocations = timesheet.assignment_snapshot.get("cost_allocations") or cls.allowed_cost_allocations(
            timesheet.worker_engagement
        )
        built_lines = []
        for raw in lines:
            raw = cls._normalize_line_allocation(raw, allocations)
            line = TimesheetLine(
                timesheet=timesheet,
                line_date=raw["line_date"],
                task_name=raw["task_name"],
                hours=raw["hours"],
                cost_center_id=raw.get("cost_center_id"),
                cost_center_code=raw.get("cost_center_code") or raw.get("costCenter") or "",
                cost_center_name=raw.get("cost_center_name") or "",
                task_code=raw.get("task_code") or raw.get("taskCode") or "",
                allocation_rationale=raw.get("allocation_rationale") or raw.get("rationale") or "",
                rate_category=raw.get("rate_category") or "",
            )
            if timesheet.period_start and timesheet.period_end and line.line_date:
                if not (timesheet.period_start <= line.line_date <= timesheet.period_end):
                    raise TimesheetValidationError({"lines": "Line date must fall within the timesheet period."})
            line.bill_amount, line.pay_amount = cls._amounts_for_line(timesheet, line)
            line.full_clean(exclude=["timesheet"] if not timesheet.pk else None)
            built_lines.append(line)

        total = sum((_decimal(line.hours) for line in built_lines), Decimal("0"))
        timesheet.total_hours = _money(total)
        timesheet.regular_hours = _money(min(total, Decimal("40.00")))
        timesheet.overtime_hours = _money(max(total - Decimal("40.00"), Decimal("0.00")))

        if persist_parent:
            timesheet.lines.all().delete()
            TimesheetLine.objects.bulk_create(built_lines)

    @classmethod
    def _normalize_line_allocation(cls, raw, allocations):
        raw = dict(raw)
        if not allocations:
            return raw

        has_allocation = any(
            raw.get(field)
            for field in ["cost_center_id", "cost_center_code", "costCenter", "task_code", "taskCode"]
        )
        if not has_allocation and len(allocations) == 1:
            allocation = allocations[0]
            raw["cost_center_id"] = allocation.get("cost_center_id")
            raw["cost_center_code"] = allocation.get("cost_center_code") or allocation.get("costCenter")
            raw["cost_center_name"] = allocation.get("cost_center_name") or allocation.get("label")
            raw["task_code"] = allocation.get("task_code") or allocation.get("taskCode")
            raw["allocation_rationale"] = "Only one cost allocation is configured on this assignment."
            return raw

        if not has_allocation:
            return raw

        requested_id = raw.get("cost_center_id")
        requested_code = (raw.get("cost_center_code") or raw.get("costCenter") or "").strip()
        requested_task_code = (raw.get("task_code") or raw.get("taskCode") or "").strip()
        for allocation in allocations:
            allocation_id = allocation.get("cost_center_id")
            allocation_code = allocation.get("cost_center_code") or allocation.get("costCenter") or ""
            allocation_task_code = allocation.get("task_code") or allocation.get("taskCode") or ""
            id_matches = requested_id and allocation_id and int(requested_id) == int(allocation_id)
            code_matches = requested_code and requested_code == allocation_code
            task_matches = not requested_task_code or requested_task_code == allocation_task_code
            if (id_matches or code_matches) and task_matches:
                raw["cost_center_id"] = allocation_id
                raw["cost_center_code"] = allocation_code
                raw["cost_center_name"] = allocation.get("cost_center_name") or allocation.get("label") or ""
                raw["task_code"] = allocation_task_code
                return raw

        raise TimesheetValidationError({"lines": "Line cost allocation is not valid for this assignment."})

    @classmethod
    def _amounts_for_line(cls, timesheet, line):
        bill_amount = None
        pay_amount = None
        if timesheet.bill_rate is not None:
            bill_amount = _money(_decimal(line.hours) * _decimal(timesheet.bill_rate))
        if timesheet.pay_rate is not None:
            pay_amount = _money(_decimal(line.hours) * _decimal(timesheet.pay_rate))
        return bill_amount, pay_amount

    @classmethod
    def _clean_or_raise(cls, timesheet):
        try:
            timesheet.full_clean()
        except DjangoValidationError as exc:
            raise TimesheetValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc

    @classmethod
    def _record_event(cls, timesheet, action, actor, note="", metadata=None):
        event = TimesheetEvent(
            timesheet=timesheet,
            actor=actor,
            action=action,
            note=note,
            metadata=metadata or {},
        )
        event.full_clean()
        event.save()
        return event

    @classmethod
    def _load_tenant_work_order(cls, engagement):
        if not engagement or engagement.engagement_type != WorkerEngagement.TYPE_WORK_ORDER:
            return None
        if not engagement.work_order_id and not engagement.work_order_number:
            return None
        from apps.workorders.models import WorkOrder

        with schema_context(engagement.tenant.schema_name):
            queryset = WorkOrder.objects.select_related("cost_center", "role_definition", "site", "legal_entity", "supplier")
            if engagement.work_order_id:
                work_order = queryset.filter(id=engagement.work_order_id).first()
                if work_order:
                    return work_order
            if engagement.work_order_number:
                return queryset.filter(work_order_number=engagement.work_order_number).first()
        return None


def _decimal(value):
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value))


def _money(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _format_decimal(value):
    if value is None:
        return None
    return format(_money(value), "f")


def _jurisdiction_for_work_order(work_order):
    if work_order.site and work_order.site.country:
        return work_order.site.country
    if work_order.legal_entity and work_order.legal_entity.country:
        return work_order.legal_entity.country
    return work_order.work_location_label or ""
