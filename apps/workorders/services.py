import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.rates.pricing import resolve_intake_rate_card_pricing, serialize_pricing_payload
from apps.workorders.approval import resolve_work_order_approval_chain
from apps.workorders.models import WorkOrder


class WorkOrderTransitionError(Exception):
    pass


class WorkOrderPermissionError(Exception):
    pass


@dataclass
class WorkOrderValidationError(Exception):
    errors: list


class WorkOrderService:
    ALLOWED_TRANSITIONS = {
        WorkOrder.STATUS_DRAFT: {WorkOrder.STATUS_SUBMITTED},
        WorkOrder.STATUS_SUBMITTED: {WorkOrder.STATUS_APPROVED, WorkOrder.STATUS_REJECTED},
        WorkOrder.STATUS_APPROVED: {WorkOrder.STATUS_ACTIVE, WorkOrder.STATUS_CLOSED},
        WorkOrder.STATUS_REJECTED: set(),
        WorkOrder.STATUS_ACTIVE: {WorkOrder.STATUS_CLOSED},
        WorkOrder.STATUS_CLOSED: set(),
    }

    @classmethod
    def create_draft(cls, *, tenant, user, attrs):
        with transaction.atomic():
            attrs = cls._apply_defaults(attrs=attrs, user=user, creating=True)
            work_order = WorkOrder(
                tenant_id=getattr(tenant, "id", None),
                created_by=user,
                **attrs,
            )
            work_order.full_clean()
            work_order.save()
            work_order.work_order_number = cls._generate_work_order_number(work_order)
            work_order.save(update_fields=["work_order_number", "updated_at"])

        cls._audit(tenant=tenant, user=user, action="work_order.created", work_order=work_order)
        return work_order

    @classmethod
    def update_draft(cls, *, tenant, user, work_order, attrs):
        if work_order.status != WorkOrder.STATUS_DRAFT:
            raise WorkOrderTransitionError("Only DRAFT work orders can be edited.")

        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            attrs = cls._apply_defaults(attrs=attrs, user=user, work_order=work_order, creating=False)
            for key, value in attrs.items():
                setattr(work_order, key, value)
            work_order.full_clean()
            work_order.save()

        cls._audit(tenant=tenant, user=user, action="work_order.updated", work_order=work_order)
        return work_order

    @classmethod
    def submit(cls, *, tenant, user, work_order):
        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            cls._ensure_transition(work_order.status, WorkOrder.STATUS_SUBMITTED)

            errors = cls._validate_for_submit(work_order)
            if errors:
                raise WorkOrderValidationError(errors)

            chain, evaluation = resolve_work_order_approval_chain(work_order)
            if not chain or not evaluation:
                raise WorkOrderValidationError(
                    [
                        {
                            "field": "approval_chain",
                            "code": "no_match",
                            "message": "No active approval chain matched this work order.",
                        }
                    ]
                )

            approval_snapshot = {
                "approval_chain_id": chain.id,
                "approval_chain_name": chain.name,
                "priority": chain.priority,
                "match_strategy": evaluation["match_strategy"],
                "condition_results": evaluation["condition_results"],
                "resolved_steps": cls._initialize_resolved_steps(evaluation["resolved_steps"]),
                "resolved_at": timezone.now().isoformat(),
            }
            current_step = cls._current_pending_step(approval_snapshot["resolved_steps"])
            approval_snapshot["current_step_sequence"] = current_step.get("sequence") if current_step else None
            approval_snapshot["approvals_remaining"] = cls._approvals_remaining(approval_snapshot["resolved_steps"])

            work_order.status = WorkOrder.STATUS_SUBMITTED
            work_order.approval_status = WorkOrder.APPROVAL_PROCESSING
            work_order.submitted_at = timezone.now()
            work_order.submitted_by = user
            work_order.approval_chain = chain
            work_order.approval_chain_snapshot = approval_snapshot
            work_order.risk_flags = cls._derive_risk_flags(work_order)
            work_order.full_clean()
            work_order.save()

        cls._audit(tenant=tenant, user=user, action="work_order.submitted", work_order=work_order)
        return work_order

    @classmethod
    def approve(cls, *, tenant, user, work_order, decision_reason=""):
        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            cls._ensure_transition(work_order.status, WorkOrder.STATUS_APPROVED)

            snapshot = work_order.approval_chain_snapshot or {}
            steps = cls._coerce_resolved_steps(snapshot.get("resolved_steps"))
            if not steps:
                raise WorkOrderValidationError(
                    [
                        {
                            "field": "approval_chain_snapshot",
                            "code": "missing_steps",
                            "message": "Approval route is missing resolved steps.",
                        }
                    ]
                )

            pending_step = cls._current_pending_step(steps)
            if not pending_step:
                raise WorkOrderTransitionError("All approval steps are already completed.")

            if not cls._can_user_approve_step(tenant=tenant, user=user, pending_step=pending_step):
                raise WorkOrderPermissionError("You are not the current approver for this work order.")

            now = timezone.now()
            pending_step["status"] = "approved"
            pending_step["approved_at"] = now.isoformat()
            pending_step["approved_by_id"] = user.id
            pending_step["approved_by_name"] = user.get_full_name().strip() or user.username

            remaining = cls._approvals_remaining(steps)
            next_step = cls._current_pending_step(steps)
            snapshot["resolved_steps"] = steps
            snapshot["approvals_remaining"] = remaining
            snapshot["current_step_sequence"] = next_step.get("sequence") if next_step else None
            snapshot["last_action"] = {
                "action": "approved_step",
                "at": now.isoformat(),
                "by_id": user.id,
                "by_name": user.get_full_name().strip() or user.username,
            }

            if remaining == 0:
                work_order.status = WorkOrder.STATUS_APPROVED
                work_order.approval_status = WorkOrder.APPROVAL_APPROVED
                work_order.decision_at = now
                work_order.decided_by = user
                work_order.decision_reason = decision_reason or ""
                work_order.supplier_acceptance_status = (
                    WorkOrder.SUPPLIER_ACCEPTANCE_PENDING
                )
                work_order.approval_chain_snapshot = snapshot
                work_order.save(
                    update_fields=[
                        "status",
                        "approval_status",
                        "supplier_acceptance_status",
                        "decision_at",
                        "decided_by",
                        "decision_reason",
                        "approval_chain_snapshot",
                        "updated_at",
                    ]
                )
                cls._audit(tenant=tenant, user=user, action="work_order.approved", work_order=work_order)
            else:
                work_order.approval_chain_snapshot = snapshot
                work_order.save(update_fields=["approval_chain_snapshot", "updated_at"])
                cls._audit(
                    tenant=tenant,
                    user=user,
                    action="work_order.approval_step_approved",
                    work_order=work_order,
                    payload={
                        "current_step_sequence": snapshot.get("current_step_sequence"),
                        "approvals_remaining": remaining,
                    },
                )

        return work_order

    @classmethod
    def reject(cls, *, tenant, user, work_order, decision_reason=""):
        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            cls._ensure_transition(work_order.status, WorkOrder.STATUS_REJECTED)

            snapshot = work_order.approval_chain_snapshot or {}
            steps = cls._coerce_resolved_steps(snapshot.get("resolved_steps"))
            pending_step = cls._current_pending_step(steps) if steps else None
            if pending_step and not cls._can_user_approve_step(tenant=tenant, user=user, pending_step=pending_step):
                raise WorkOrderPermissionError("You are not the current approver for this work order.")

            now = timezone.now()
            if pending_step:
                pending_step["status"] = "rejected"
                pending_step["approved_at"] = now.isoformat()
                pending_step["approved_by_id"] = user.id
                pending_step["approved_by_name"] = user.get_full_name().strip() or user.username
                snapshot["resolved_steps"] = steps
                snapshot["approvals_remaining"] = cls._approvals_remaining(steps)
                snapshot["current_step_sequence"] = None
                snapshot["last_action"] = {
                    "action": "rejected",
                    "at": now.isoformat(),
                    "by_id": user.id,
                    "by_name": user.get_full_name().strip() or user.username,
                }

            work_order.status = WorkOrder.STATUS_REJECTED
            work_order.approval_status = WorkOrder.APPROVAL_REJECTED
            work_order.decision_at = now
            work_order.decided_by = user
            work_order.decision_reason = decision_reason or ""
            work_order.approval_chain_snapshot = snapshot
            work_order.save(
                update_fields=[
                    "status",
                    "approval_status",
                    "decision_at",
                    "decided_by",
                    "decision_reason",
                    "approval_chain_snapshot",
                    "updated_at",
                ]
            )

        cls._audit(tenant=tenant, user=user, action="work_order.rejected", work_order=work_order)
        return work_order

    @classmethod
    def activate(cls, *, tenant, user, work_order):
        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            cls._ensure_transition(work_order.status, WorkOrder.STATUS_ACTIVE)
            work_order.status = WorkOrder.STATUS_ACTIVE
            work_order.save(update_fields=["status", "updated_at"])
        cls._audit(tenant=tenant, user=user, action="work_order.activated", work_order=work_order)
        return work_order

    @classmethod
    def close(cls, *, tenant, user, work_order):
        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
            cls._ensure_transition(work_order.status, WorkOrder.STATUS_CLOSED)
            work_order.status = WorkOrder.STATUS_CLOSED
            work_order.save(update_fields=["status", "updated_at"])
        cls._audit(tenant=tenant, user=user, action="work_order.closed", work_order=work_order)
        return work_order

    @classmethod
    def _ensure_transition(cls, current, target):
        if target in cls.ALLOWED_TRANSITIONS.get(current, set()):
            return
        raise WorkOrderTransitionError(f"Invalid transition: {current} -> {target}")

    @classmethod
    def _apply_defaults(cls, *, attrs, user, work_order=None, creating=False):
        attrs = dict(attrs or {})
        intake = attrs.get("intake") or (work_order.intake if work_order else None)
        selected_candidate = attrs.get("selected_candidate") or (work_order.selected_candidate if work_order else None)
        pricing_context = None

        if selected_candidate and intake and selected_candidate.intake_id != intake.id:
            raise WorkOrderValidationError(
                [
                    {
                        "field": "selected_candidate",
                        "code": "intake_mismatch",
                        "message": "Selected candidate must belong to the same intake.",
                    }
                ]
            )

        if (
            selected_candidate
            and selected_candidate.status != selected_candidate.STATUS_ACCEPTED
        ):
            raise WorkOrderValidationError(
                [
                    {
                        "field": "selected_candidate",
                        "code": "not_selected",
                        "message": (
                            "Candidate must be selected by a buyer before a work "
                            "order can be created."
                        ),
                    }
                ]
            )

        if selected_candidate and not intake:
            intake = selected_candidate.intake
            attrs["intake"] = intake

        if intake:
            attrs.setdefault("supplier", intake.supplier)
            attrs.setdefault("role_definition", intake.role_definition)
            attrs.setdefault("cost_center", intake.cost_center)
            attrs.setdefault("legal_entity", intake.legal_entity)
            attrs.setdefault("site", intake.site)
            attrs.setdefault("start_date", intake.start_date)
            attrs.setdefault("end_date", intake.end_date)
            attrs.setdefault("budget_amount", intake.budget_amount)
            attrs.setdefault("currency", intake.currency)
            if intake.site and not attrs.get("work_location_label"):
                attrs["work_location_label"] = intake.site.name
            pricing_context = resolve_intake_rate_card_pricing(
                intake=intake,
                supplier=attrs.get("supplier"),
                work_location_label=attrs.get("work_location_label"),
                strict=True,
            )
            if pricing_context and (creating or attrs.get("bill_rate") is None):
                attrs["bill_rate"] = pricing_context["bill_rate"]
            elif intake.target_rate is not None and attrs.get("bill_rate") is None:
                attrs["bill_rate"] = intake.target_rate

        if selected_candidate:
            attrs.setdefault("supplier", selected_candidate.supplier)
            if not attrs.get("worker_full_name"):
                attrs["worker_full_name"] = selected_candidate.full_name
            if not attrs.get("worker_email"):
                attrs["worker_email"] = selected_candidate.email
            if not attrs.get("worker_phone"):
                attrs["worker_phone"] = selected_candidate.phone
            if not attrs.get("resume_url"):
                attrs["resume_url"] = selected_candidate.resume_url
            if attrs.get("pay_rate") is None and selected_candidate.proposed_rate is not None:
                attrs["pay_rate"] = selected_candidate.proposed_rate
            if not attrs.get("currency") and selected_candidate.currency:
                attrs["currency"] = selected_candidate.currency

        if attrs.get("currency"):
            attrs["currency"] = str(attrs["currency"]).strip().upper()

        # Keep estimated cost/risk flags in sync whenever pricing inputs change.
        if (
            "bill_rate" in attrs
            or "hours_per_week" in attrs
            or "start_date" in attrs
            or "end_date" in attrs
            or "overtime_enabled" in attrs
            or "overtime_multiplier" in attrs
        ):
            estimated_cost = cls._estimate_cost(
                bill_rate=attrs.get("bill_rate", getattr(work_order, "bill_rate", None) if work_order else None),
                hours_per_week=attrs.get(
                    "hours_per_week", getattr(work_order, "hours_per_week", None) if work_order else None
                ),
                start_date=attrs.get("start_date", getattr(work_order, "start_date", None) if work_order else None),
                end_date=attrs.get("end_date", getattr(work_order, "end_date", None) if work_order else None),
                overtime_enabled=attrs.get(
                    "overtime_enabled",
                    getattr(work_order, "overtime_enabled", False) if work_order else False,
                ),
                overtime_multiplier=attrs.get(
                    "overtime_multiplier",
                    getattr(work_order, "overtime_multiplier", None) if work_order else None,
                ),
            )
            if estimated_cost is not None:
                attrs["estimated_cost"] = estimated_cost

        if creating or "source_snapshot" not in attrs:
            attrs["source_snapshot"] = cls._build_source_snapshot(
                intake=intake,
                selected_candidate=selected_candidate,
                attrs=attrs,
                created_by=user,
                pricing_context=pricing_context,
                existing_snapshot=(work_order.source_snapshot if work_order else None),
            )

        if creating:
            attrs.setdefault("status", WorkOrder.STATUS_DRAFT)
            attrs.setdefault("approval_status", WorkOrder.APPROVAL_NOT_STARTED)
            attrs.setdefault("approval_chain_snapshot", {})
            attrs.setdefault("risk_flags", [])

        if "risk_flags" not in attrs and work_order is not None:
            attrs["risk_flags"] = cls._derive_risk_flags_from_values(
                budget_amount=attrs.get("budget_amount", work_order.budget_amount),
                estimated_cost=attrs.get("estimated_cost", work_order.estimated_cost),
                bill_rate=attrs.get("bill_rate", work_order.bill_rate),
                pay_rate=attrs.get("pay_rate", work_order.pay_rate),
                overtime_enabled=attrs.get("overtime_enabled", work_order.overtime_enabled),
            )

        if "worker_full_name" in attrs:
            attrs["worker_full_name"] = (attrs.get("worker_full_name") or "").strip()
        if "worker_phone" in attrs:
            attrs["worker_phone"] = (attrs.get("worker_phone") or "").strip()
        if "notes" in attrs:
            attrs["notes"] = (attrs.get("notes") or "").strip()
        if "work_location_label" in attrs:
            attrs["work_location_label"] = (attrs.get("work_location_label") or "").strip()

        return attrs

    @classmethod
    def _validate_for_submit(cls, work_order):
        required_fields = [
            ("intake", "Intake is required."),
            ("selected_candidate", "Selected candidate is required."),
            ("supplier", "Supplier is required."),
            ("role_definition", "Role is required."),
            ("worker_full_name", "Worker full name is required."),
            ("worker_email", "Worker email is required."),
            ("start_date", "Start date is required."),
            ("end_date", "End date is required."),
            ("bill_rate", "Bill rate is required."),
            ("pay_rate", "Pay rate is required."),
            ("currency", "Currency is required."),
            ("hours_per_week", "Hours per week is required."),
        ]

        errors = []
        for field, message in required_fields:
            value = getattr(work_order, field)
            if value in (None, ""):
                errors.append({"field": field, "code": "required", "message": message})

        if work_order.hours_per_week is not None and work_order.hours_per_week <= 0:
            errors.append(
                {
                    "field": "hours_per_week",
                    "code": "invalid",
                    "message": "Hours per week must be greater than 0.",
                }
            )

        if work_order.selected_candidate and work_order.intake:
            if work_order.selected_candidate.intake_id != work_order.intake_id:
                errors.append(
                    {
                        "field": "selected_candidate",
                        "code": "intake_mismatch",
                        "message": "Selected candidate must belong to the selected intake.",
                    }
                )

        if (
            work_order.selected_candidate
            and work_order.selected_candidate.status
            != work_order.selected_candidate.STATUS_ACCEPTED
        ):
            errors.append(
                {
                    "field": "selected_candidate",
                    "code": "not_selected",
                    "message": (
                        "Candidate must be selected by a buyer before the work "
                        "order can be submitted."
                    ),
                }
            )

        if work_order.selected_candidate and work_order.supplier:
            if work_order.selected_candidate.supplier_id != work_order.supplier_id:
                errors.append(
                    {
                        "field": "supplier",
                        "code": "supplier_mismatch",
                        "message": "Supplier must match the selected candidate supplier.",
                    }
                )

        if work_order.start_date and work_order.end_date and work_order.end_date < work_order.start_date:
            errors.append(
                {
                    "field": "end_date",
                    "code": "invalid_range",
                    "message": "End date cannot be earlier than start date.",
                }
            )

        return errors

    @staticmethod
    def _initialize_resolved_steps(raw_steps):
        initialized = []
        for step in raw_steps or []:
            initialized.append(
                {
                    "sequence": step.get("sequence"),
                    "step_type": step.get("step_type"),
                    "approver_id": step.get("approver_id"),
                    "approver_name": step.get("approver_name"),
                    "amount": step.get("amount"),
                    "currency": step.get("currency"),
                    "status": "pending",
                    "approved_at": None,
                    "approved_by_id": None,
                    "approved_by_name": "",
                }
            )
        return initialized

    @staticmethod
    def _coerce_resolved_steps(raw_steps):
        steps = []
        for step in raw_steps or []:
            status = step.get("status") or ("approved" if step.get("approved_at") else "pending")
            steps.append(
                {
                    "sequence": step.get("sequence"),
                    "step_type": step.get("step_type"),
                    "approver_id": step.get("approver_id"),
                    "approver_name": step.get("approver_name"),
                    "amount": step.get("amount"),
                    "currency": step.get("currency"),
                    "status": status,
                    "approved_at": step.get("approved_at"),
                    "approved_by_id": step.get("approved_by_id"),
                    "approved_by_name": step.get("approved_by_name"),
                }
            )
        return sorted(steps, key=lambda item: (item.get("sequence") or 0, item.get("approver_id") or 0))

    @staticmethod
    def _current_pending_step(steps):
        for step in sorted(steps or [], key=lambda item: (item.get("sequence") or 0, item.get("approver_id") or 0)):
            if step.get("status") not in {"approved", "rejected"}:
                return step
        return None

    @staticmethod
    def _approvals_remaining(steps):
        return sum(1 for step in (steps or []) if step.get("status") not in {"approved", "rejected"})

    @staticmethod
    def _can_user_approve_step(*, tenant, user, pending_step):
        if not user or not pending_step:
            return False
        if user.is_superuser or user.id == pending_step.get("approver_id"):
            return True

        from apps.accounts.models import Membership

        return Membership.objects.filter(
            user=user,
            tenant=tenant,
            role=Membership.ROLE_ADMIN,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        ).exists()

    @staticmethod
    def _estimate_cost(*, bill_rate, hours_per_week, start_date, end_date, overtime_enabled, overtime_multiplier):
        if bill_rate is None or hours_per_week is None or not start_date or not end_date:
            return None
        if end_date < start_date:
            return None
        weeks = Decimal((end_date - start_date).days + 1) / Decimal("7")
        base_cost = Decimal(str(bill_rate)) * Decimal(str(hours_per_week)) * weeks
        if overtime_enabled and overtime_multiplier:
            overtime_factor = Decimal("1.0") + (Decimal(str(overtime_multiplier)) - Decimal("1.0")) * Decimal("0.2")
            base_cost = base_cost * overtime_factor
        return base_cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def _derive_risk_flags(cls, work_order):
        return cls._derive_risk_flags_from_values(
            budget_amount=work_order.budget_amount,
            estimated_cost=work_order.estimated_cost,
            bill_rate=work_order.bill_rate,
            pay_rate=work_order.pay_rate,
            overtime_enabled=work_order.overtime_enabled,
        )

    @staticmethod
    def _derive_risk_flags_from_values(*, budget_amount, estimated_cost, bill_rate, pay_rate, overtime_enabled):
        flags = []
        if budget_amount is not None and estimated_cost is not None:
            budget = Decimal(str(budget_amount))
            estimate = Decimal(str(estimated_cost))
            if budget > 0:
                ratio = estimate / budget
                if ratio > Decimal("1.0"):
                    flags.append("Estimated cost exceeds budget cap")
                elif ratio >= Decimal("0.8"):
                    flags.append("Budget cap within 20%")

        if bill_rate is not None and pay_rate is not None:
            if Decimal(str(pay_rate)) > Decimal(str(bill_rate)):
                flags.append("Pay rate exceeds bill rate")

        if overtime_enabled:
            flags.append("Overtime enabled")

        return flags

    @staticmethod
    def _build_source_snapshot(*, intake, selected_candidate, attrs, created_by, pricing_context=None, existing_snapshot=None):
        if not intake and not selected_candidate and existing_snapshot is not None:
            return existing_snapshot

        payload = dict(existing_snapshot or {})
        payload.update(
            {
                "captured_at": timezone.now().isoformat(),
                "captured_by_id": created_by.id if created_by else None,
                "captured_by_name": (
                    (created_by.get_full_name().strip() or created_by.username) if created_by else ""
                ),
            }
        )

        if intake:
            payload["intake"] = {
                "id": intake.id,
                "title": intake.title,
                "description": intake.description,
                "engagement_type": intake.engagement_type,
                "status": intake.status,
                "approval_status": intake.approval_status,
                "start_date": intake.start_date.isoformat() if intake.start_date else None,
                "end_date": intake.end_date.isoformat() if intake.end_date else None,
                "worker_count": intake.worker_count,
                "target_rate": str(intake.target_rate) if intake.target_rate is not None else None,
                "rate_unit": intake.rate_unit,
                "budget_amount": str(intake.budget_amount) if intake.budget_amount is not None else None,
                "currency": intake.currency,
                "country": intake.country,
                "state_province": intake.state_province,
                "city": intake.city,
                "custom_fields": intake.custom_fields or {},
                "approval_chain_id": intake.approval_chain_id,
            }

        if selected_candidate:
            payload["selected_candidate"] = {
                "id": selected_candidate.id,
                "intake_id": selected_candidate.intake_id,
                "supplier_id": selected_candidate.supplier_id,
                "full_name": selected_candidate.full_name,
                "email": selected_candidate.email,
                "phone": selected_candidate.phone,
                "notes": selected_candidate.notes,
                "resume_url": selected_candidate.resume_url,
                "available_start_date": (
                    selected_candidate.available_start_date.isoformat()
                    if selected_candidate.available_start_date
                    else None
                ),
                "proposed_rate": str(selected_candidate.proposed_rate)
                if selected_candidate.proposed_rate is not None
                else None,
                "currency": selected_candidate.currency,
                "status": selected_candidate.status,
            }

        if pricing_context:
            payload["pricing"] = serialize_pricing_payload(pricing_context)
        elif intake is not None:
            payload.pop("pricing", None)

        payload["effective_values"] = {
            "worker_full_name": attrs.get("worker_full_name"),
            "worker_email": attrs.get("worker_email"),
            "worker_phone": attrs.get("worker_phone"),
            "start_date": attrs["start_date"].isoformat() if attrs.get("start_date") else None,
            "end_date": attrs["end_date"].isoformat() if attrs.get("end_date") else None,
            "bill_rate": str(attrs["bill_rate"]) if attrs.get("bill_rate") is not None else None,
            "pay_rate": str(attrs["pay_rate"]) if attrs.get("pay_rate") is not None else None,
            "currency": attrs.get("currency"),
            "hours_per_week": str(attrs["hours_per_week"]) if attrs.get("hours_per_week") is not None else None,
            "budget_amount": str(attrs["budget_amount"]) if attrs.get("budget_amount") is not None else None,
            "work_location_label": attrs.get("work_location_label", ""),
        }
        return payload

    @staticmethod
    def _generate_work_order_number(work_order):
        year = timezone.now().year
        return f"WO-{year}-{work_order.id:05d}"

    @classmethod
    def _snapshot_payload(cls, work_order):
        return {
            "id": work_order.id,
            "work_order_number": work_order.work_order_number,
            "status": work_order.status,
            "approval_status": work_order.approval_status,
            "intake_id": work_order.intake_id,
            "selected_candidate_id": work_order.selected_candidate_id,
            "supplier_id": work_order.supplier_id,
            "worker_full_name": work_order.worker_full_name,
            "worker_email": work_order.worker_email,
            "worker_phone": work_order.worker_phone,
            "role_definition_id": work_order.role_definition_id,
            "start_date": work_order.start_date.isoformat() if work_order.start_date else None,
            "end_date": work_order.end_date.isoformat() if work_order.end_date else None,
            "bill_rate": str(work_order.bill_rate) if work_order.bill_rate is not None else None,
            "pay_rate": str(work_order.pay_rate) if work_order.pay_rate is not None else None,
            "currency": work_order.currency,
            "hours_per_week": str(work_order.hours_per_week) if work_order.hours_per_week is not None else None,
            "overtime_enabled": work_order.overtime_enabled,
            "overtime_multiplier": str(work_order.overtime_multiplier)
            if work_order.overtime_multiplier is not None
            else None,
            "estimated_cost": str(work_order.estimated_cost) if work_order.estimated_cost is not None else None,
            "budget_amount": str(work_order.budget_amount) if work_order.budget_amount is not None else None,
            "cost_center_id": work_order.cost_center_id,
            "legal_entity_id": work_order.legal_entity_id,
            "site_id": work_order.site_id,
            "work_location_label": work_order.work_location_label,
            "notes": work_order.notes,
            "resume_url": work_order.resume_url,
            "approval_chain_id": work_order.approval_chain_id,
            "risk_flags": work_order.risk_flags or [],
            "source_snapshot": work_order.source_snapshot or {},
            "approval_chain_snapshot": work_order.approval_chain_snapshot or {},
        }

    @classmethod
    def _audit(cls, *, tenant, user, action, work_order, payload=None):
        snapshot_payload = cls._snapshot_payload(work_order)
        if payload:
            snapshot_payload["event_payload"] = payload
        payload_hash = hashlib.sha256(
            json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=action,
            object_type="work_order",
            object_id=str(work_order.id),
            payload_hash=payload_hash,
        )
