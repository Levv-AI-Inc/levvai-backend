import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.intake.models import IntakeRequest, IntakeSnapshot
from apps.intake.validation import validate_intake_request


class IntakeTransitionError(Exception):
    pass


@dataclass
class IntakeValidationError(Exception):
    errors: list


class IntakeService:
    ALLOWED_TRANSITIONS = {
        IntakeRequest.STATUS_DRAFT: {IntakeRequest.STATUS_SUBMITTED},
        IntakeRequest.STATUS_SUBMITTED: {
            IntakeRequest.STATUS_APPROVED,
            IntakeRequest.STATUS_REJECTED,
        },
        IntakeRequest.STATUS_APPROVED: set(),
        IntakeRequest.STATUS_REJECTED: set(),
    }

    @classmethod
    def create_draft(cls, *, tenant, user, attrs):
        intake = IntakeRequest.objects.create(
            tenant_id=getattr(tenant, "id", None),
            created_by=user,
            **attrs,
        )
        cls._audit(tenant=tenant, user=user, action="intake.created", intake=intake)
        return intake

    @classmethod
    def update_draft(cls, *, tenant, user, intake, attrs):
        if intake.status != IntakeRequest.STATUS_DRAFT:
            raise IntakeTransitionError("Only DRAFT intakes can be edited.")

        for key, value in attrs.items():
            setattr(intake, key, value)
        intake.save()

        warnings = validate_intake_request(intake, strict=False)
        cls._audit(tenant=tenant, user=user, action="intake.updated", intake=intake)
        return intake, warnings

    @classmethod
    def submit(cls, *, tenant, user, intake):
        with transaction.atomic():
            intake = IntakeRequest.objects.select_for_update().get(pk=intake.pk)
            cls._ensure_transition(intake.status, IntakeRequest.STATUS_SUBMITTED)

            errors = validate_intake_request(intake, strict=True)
            if errors:
                raise IntakeValidationError(errors)

            snapshot = cls._create_snapshot(intake=intake, user=user)

            intake.status = IntakeRequest.STATUS_SUBMITTED
            intake.submitted_at = timezone.now()
            intake.submitted_by = user
            intake.save(update_fields=["status", "submitted_at", "submitted_by", "updated_at"])

            cls._audit(
                tenant=tenant,
                user=user,
                action="intake.submitted",
                intake=intake,
                payload={
                    "snapshot_id": snapshot.id,
                    "version": snapshot.version,
                },
            )

        return intake

    @classmethod
    def approve(cls, *, tenant, user, intake, decision_reason=""):
        with transaction.atomic():
            intake = IntakeRequest.objects.select_for_update().get(pk=intake.pk)
            cls._ensure_transition(intake.status, IntakeRequest.STATUS_APPROVED)

            intake.status = IntakeRequest.STATUS_APPROVED
            intake.decision_at = timezone.now()
            intake.decided_by = user
            intake.decision_reason = decision_reason or ""
            intake.save(update_fields=["status", "decision_at", "decided_by", "decision_reason", "updated_at"])

            cls._audit(tenant=tenant, user=user, action="intake.approved", intake=intake)

        return intake

    @classmethod
    def reject(cls, *, tenant, user, intake, decision_reason=""):
        with transaction.atomic():
            intake = IntakeRequest.objects.select_for_update().get(pk=intake.pk)
            cls._ensure_transition(intake.status, IntakeRequest.STATUS_REJECTED)

            intake.status = IntakeRequest.STATUS_REJECTED
            intake.decision_at = timezone.now()
            intake.decided_by = user
            intake.decision_reason = decision_reason or ""
            intake.save(update_fields=["status", "decision_at", "decided_by", "decision_reason", "updated_at"])

            cls._audit(tenant=tenant, user=user, action="intake.rejected", intake=intake)

        return intake

    @classmethod
    def _ensure_transition(cls, current, target):
        if target in cls.ALLOWED_TRANSITIONS.get(current, set()):
            return
        raise IntakeTransitionError(f"Invalid transition: {current} -> {target}")

    @classmethod
    def _create_snapshot(cls, *, intake, user):
        version = (intake.snapshots.aggregate(max_version=Max("version")).get("max_version") or 0) + 1
        snapshot = IntakeSnapshot.objects.create(
            intake=intake,
            version=version,
            snapshot_json=cls._snapshot_payload(intake),
            created_by=user,
        )
        return snapshot

    @classmethod
    def _snapshot_payload(cls, intake):
        return {
            "id": intake.id,
            "status": intake.status,
            "engagement_type": intake.engagement_type,
            "cost_center_id": intake.cost_center_id,
            "site_id": intake.site_id,
            "supplier_id": intake.supplier_id,
            "title": intake.title,
            "description": intake.description,
            "start_date": intake.start_date.isoformat() if intake.start_date else None,
            "end_date": intake.end_date.isoformat() if intake.end_date else None,
            "worker_count": intake.worker_count,
            "target_rate": str(intake.target_rate) if intake.target_rate is not None else None,
            "rate_unit": intake.rate_unit,
            "budget_amount": str(intake.budget_amount) if intake.budget_amount is not None else None,
            "currency": intake.currency,
            "custom_fields": intake.custom_fields or {},
            "submitted_at": timezone.now().isoformat(),
        }

    @classmethod
    def _audit(cls, *, tenant, user, action, intake, payload=None):
        payload = payload or cls._snapshot_payload(intake)
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=action,
            object_type="intake_request",
            object_id=str(intake.id),
            payload_hash=payload_hash,
        )
