import hashlib
import json
import logging
from dataclasses import dataclass
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.intake.approval import resolve_intake_approval_chain
from apps.intake.models import (
    IntakeQualification,
    IntakeRequest,
    IntakeSelectedCandidate,
    IntakeSnapshot,
)
from apps.intake.validation import validate_intake_request

logger = logging.getLogger(__name__)


class IntakeTransitionError(Exception):
    pass


class IntakePermissionError(Exception):
    pass


class CandidateTransitionError(Exception):
    pass


@dataclass
class IntakeValidationError(Exception):
    errors: list


class CandidateService:
    ALLOWED_TRANSITIONS = {
        IntakeSelectedCandidate.STATUS_SUBMITTED: {
            IntakeSelectedCandidate.STATUS_REVIEWED,
            IntakeSelectedCandidate.STATUS_REJECTED,
        },
        IntakeSelectedCandidate.STATUS_REVIEWED: {
            IntakeSelectedCandidate.STATUS_ACCEPTED,
            IntakeSelectedCandidate.STATUS_REJECTED,
        },
        IntakeSelectedCandidate.STATUS_ACCEPTED: {
            IntakeSelectedCandidate.STATUS_REVIEWED,
            IntakeSelectedCandidate.STATUS_REJECTED,
        },
        IntakeSelectedCandidate.STATUS_REJECTED: {
            IntakeSelectedCandidate.STATUS_REVIEWED,
        },
    }

    @classmethod
    def transition(cls, *, tenant, user, candidate, target_status):
        from apps.workorders.models import WorkOrder

        with transaction.atomic():
            candidate = (
                IntakeSelectedCandidate.objects.select_for_update()
                .select_related("intake")
                .get(pk=candidate.pk)
            )
            if target_status == candidate.status:
                return candidate
            if target_status not in cls.ALLOWED_TRANSITIONS.get(candidate.status, set()):
                raise CandidateTransitionError(
                    f"Candidate cannot move from {candidate.status} to {target_status}."
                )

            linked_work_order = WorkOrder.objects.filter(
                selected_candidate=candidate
            ).first()
            if linked_work_order and target_status != IntakeSelectedCandidate.STATUS_ACCEPTED:
                raise CandidateTransitionError(
                    "Candidate status cannot change after a work order has been created."
                )

            if target_status == IntakeSelectedCandidate.STATUS_ACCEPTED:
                conflicting_work_order = (
                    WorkOrder.objects.filter(intake_id=candidate.intake_id)
                    .exclude(selected_candidate=candidate)
                    .first()
                )
                if conflicting_work_order:
                    raise CandidateTransitionError(
                        "Another candidate is already attached to a work order for this job posting."
                    )

                other_selected = list(
                    IntakeSelectedCandidate.objects.select_for_update()
                    .filter(
                        intake_id=candidate.intake_id,
                        status=IntakeSelectedCandidate.STATUS_ACCEPTED,
                    )
                    .exclude(pk=candidate.pk)
                )
                for selected in other_selected:
                    if WorkOrder.objects.filter(selected_candidate=selected).exists():
                        raise CandidateTransitionError(
                            "Another selected candidate already has a work order for this job posting."
                        )
                if other_selected:
                    IntakeSelectedCandidate.objects.filter(
                        pk__in=[selected.pk for selected in other_selected]
                    ).update(
                        status=IntakeSelectedCandidate.STATUS_REVIEWED,
                        updated_at=timezone.now(),
                    )

            previous_status = candidate.status
            candidate.status = target_status
            candidate.save(update_fields=["status", "updated_at"])

        cls._audit(
            tenant=tenant,
            user=user,
            candidate=candidate,
            previous_status=previous_status,
        )
        return candidate

    @staticmethod
    def _audit(*, tenant, user, candidate, previous_status):
        payload = {
            "candidate_id": candidate.id,
            "intake_id": candidate.intake_id,
            "supplier_id": candidate.supplier_id,
            "previous_status": previous_status,
            "status": candidate.status,
        }
        payload_hash = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=f"candidate.{candidate.status}",
            object_type="intake_selected_candidate",
            object_id=str(candidate.id),
            payload_hash=payload_hash,
        )


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
        qualifications = attrs.pop("qualifications", None)
        with transaction.atomic():
            attrs = cls._apply_defaults(attrs)
            intake = IntakeRequest.objects.create(
                tenant_id=getattr(tenant, "id", None),
                created_by=user,
                **attrs,
            )
            if qualifications is not None:
                cls._replace_qualifications(intake=intake, qualifications=qualifications)
        cls._audit(tenant=tenant, user=user, action="intake.created", intake=intake)
        return intake

    @classmethod
    def update_draft(cls, *, tenant, user, intake, attrs):
        if intake.status != IntakeRequest.STATUS_DRAFT:
            raise IntakeTransitionError("Only DRAFT intakes can be edited.")

        qualifications = attrs.pop("qualifications", None)
        with transaction.atomic():
            attrs = cls._apply_defaults(attrs, intake=intake)
            for key, value in attrs.items():
                setattr(intake, key, value)
            intake.save()
            if qualifications is not None:
                cls._replace_qualifications(intake=intake, qualifications=qualifications)

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

            chain, evaluation = resolve_intake_approval_chain(intake)
            if not chain or not evaluation:
                raise IntakeValidationError(
                    [
                        {
                            "field": "approval_chain",
                            "code": "no_match",
                            "message": "No active approval chain matched this request.",
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

            snapshot = cls._create_snapshot(intake=intake, user=user)

            intake.status = IntakeRequest.STATUS_SUBMITTED
            intake.approval_status = "processing"
            intake.submitted_at = timezone.now()
            intake.submitted_by = user
            intake.approval_chain = chain
            intake.approval_chain_snapshot = approval_snapshot
            intake.approval_started_at = timezone.now()
            intake.save(
                update_fields=[
                    "status",
                    "approval_status",
                    "submitted_at",
                    "submitted_by",
                    "approval_chain",
                    "approval_chain_snapshot",
                    "approval_started_at",
                    "updated_at",
                ]
            )

            cls._audit(
                tenant=tenant,
                user=user,
                action="intake.submitted",
                intake=intake,
                payload={
                    "snapshot_id": snapshot.id,
                    "version": snapshot.version,
                    "approval_chain_id": chain.id,
                },
            )

        return intake

    @classmethod
    def approve(cls, *, tenant, user, intake, decision_reason="", portal_base_url=""):
        with transaction.atomic():
            intake = IntakeRequest.objects.select_for_update().get(pk=intake.pk)
            cls._ensure_transition(intake.status, IntakeRequest.STATUS_APPROVED)

            snapshot = intake.approval_chain_snapshot or {}
            steps = cls._coerce_resolved_steps(snapshot.get("resolved_steps"))
            if not steps:
                raise IntakeValidationError(
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
                raise IntakeTransitionError("All approval steps are already completed.")

            if not cls._can_user_approve_step(tenant=tenant, user=user, pending_step=pending_step):
                raise IntakePermissionError("You are not the current approver for this request.")

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
                intake.status = IntakeRequest.STATUS_APPROVED
                intake.approval_status = "approved"
                intake.decision_at = now
                intake.decided_by = user
                intake.decision_reason = decision_reason or ""
                intake.approval_chain_snapshot = snapshot
                intake.save(
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
                if intake.engagement_type == IntakeRequest.ENGAGEMENT_STAFFING:
                    transaction.on_commit(
                        lambda: cls._notify_suppliers_job_posting_approved(
                            tenant=tenant,
                            intake=intake,
                            approved_by=user,
                            portal_base_url=portal_base_url,
                        )
                    )
                cls._audit(tenant=tenant, user=user, action="intake.approved", intake=intake)
            else:
                intake.approval_chain_snapshot = snapshot
                intake.save(update_fields=["approval_chain_snapshot", "updated_at"])
                cls._audit(
                    tenant=tenant,
                    user=user,
                    action="intake.approval_step_approved",
                    intake=intake,
                    payload={
                        "current_step_sequence": snapshot.get("current_step_sequence"),
                        "approvals_remaining": remaining,
                    },
                )

        return intake

    @classmethod
    def reject(cls, *, tenant, user, intake, decision_reason=""):
        with transaction.atomic():
            intake = IntakeRequest.objects.select_for_update().get(pk=intake.pk)
            cls._ensure_transition(intake.status, IntakeRequest.STATUS_REJECTED)

            snapshot = intake.approval_chain_snapshot or {}
            steps = cls._coerce_resolved_steps(snapshot.get("resolved_steps"))
            pending_step = cls._current_pending_step(steps) if steps else None
            if pending_step and not cls._can_user_approve_step(tenant=tenant, user=user, pending_step=pending_step):
                raise IntakePermissionError("You are not the current approver for this request.")

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

            intake.status = IntakeRequest.STATUS_REJECTED
            intake.approval_status = "rejected"
            intake.decision_at = now
            intake.decided_by = user
            intake.decision_reason = decision_reason or ""
            intake.approval_chain_snapshot = snapshot
            intake.save(
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

            cls._audit(tenant=tenant, user=user, action="intake.rejected", intake=intake)

        return intake

    @classmethod
    def _ensure_transition(cls, current, target):
        if target in cls.ALLOWED_TRANSITIONS.get(current, set()):
            return
        raise IntakeTransitionError(f"Invalid transition: {current} -> {target}")

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
            "approval_status": intake.approval_status,
            "approval_chain_id": intake.approval_chain_id,
            "cost_center_id": intake.cost_center_id,
            "site_id": intake.site_id,
            "supplier_id": intake.supplier_id,
            "role_definition_id": intake.role_definition_id,
            "legal_entity_id": intake.legal_entity_id,
            "title": intake.title,
            "description": intake.description,
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
            "rate_card_id": intake.rate_card_id,
            "overtime_enabled": intake.overtime_enabled,
            "overtime_multiplier": str(intake.overtime_multiplier) if intake.overtime_multiplier is not None else None,
            "custom_fields": intake.custom_fields or {},
            "qualifications_enabled": intake.qualifications_enabled,
            "qualifications": [
                {
                    "id": qualification.id,
                    "sequence": qualification.sequence,
                    "name": qualification.name,
                    "qualification_type": qualification.qualification_type,
                    "group": qualification.group,
                    "description": qualification.description,
                    "mandatory": qualification.mandatory,
                    "knockout": qualification.knockout,
                    "response_mode": qualification.response_mode,
                    "min_years": qualification.min_years,
                    "proficiency": qualification.proficiency,
                    "weight": qualification.weight,
                    "tags": qualification.tags or [],
                }
                for qualification in intake.qualifications.all().order_by("sequence", "id")
            ],
            "approval_chain_snapshot": intake.approval_chain_snapshot or {},
            "submitted_at": timezone.now().isoformat(),
        }

    @classmethod
    def _replace_qualifications(cls, *, intake, qualifications):
        intake.qualifications.all().delete()
        for item in qualifications:
            qualification = IntakeQualification(intake=intake, **item)
            qualification.full_clean()
            qualification.save()

    @classmethod
    def _apply_defaults(cls, attrs, intake=None):
        role_definition = attrs.get("role_definition")
        site = attrs.get("site")
        legal_entity = attrs.get("legal_entity")

        if intake is not None:
            if role_definition is None:
                role_definition = intake.role_definition
            if site is None:
                site = intake.site
            if legal_entity is None:
                legal_entity = intake.legal_entity

        title_provided = "title" in attrs
        if title_provided:
            attrs["title"] = (attrs.get("title") or "").strip()
        elif intake is None:
            attrs["title"] = ""

        description = attrs.get("description")
        if description is not None:
            attrs["description"] = (description or "").strip()

        current_title = attrs.get("title") if title_provided else (intake.title if intake else "")
        if not (current_title or "").strip() and role_definition:
            city = attrs.get("city") or (site.city if site else "")
            if city:
                attrs["title"] = f"{role_definition.name} - {city}"
            else:
                attrs["title"] = role_definition.name

        if site:
            if ("country" in attrs and not attrs.get("country")) or (intake is None and "country" not in attrs):
                attrs["country"] = site.country or ""
            if ("state_province" in attrs and not attrs.get("state_province")) or (
                intake is None and "state_province" not in attrs
            ):
                attrs["state_province"] = site.state_province or ""
            if ("city" in attrs and not attrs.get("city")) or (intake is None and "city" not in attrs):
                attrs["city"] = site.city or ""

        if not legal_entity and site and site.legal_entity and (
            intake is None or "site" in attrs or not intake.legal_entity_id
        ):
            attrs["legal_entity"] = site.legal_entity

        if role_definition:
            currency = attrs.get("currency")
            if not currency:
                attrs["currency"] = role_definition.default_currency

            rate_unit = attrs.get("rate_unit")
            if not rate_unit:
                attrs["rate_unit"] = (
                    IntakeRequest.RATE_DAILY
                    if role_definition.default_unit == "day"
                    else IntakeRequest.RATE_HOURLY
                )

        return attrs

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

    @classmethod
    def _notify_suppliers_job_posting_approved(cls, *, tenant, intake, approved_by, portal_base_url):
        try:
            recipients = cls._supplier_notification_recipients(tenant=tenant, intake=intake)
            if not recipients:
                return

            job_link = cls._job_posting_link(intake=intake, portal_base_url=portal_base_url)
            subject = f"Job Posting Approved: {intake.title or f'Request #{intake.id}'}"
            text_body = cls._job_posting_approved_email_text(
                tenant_name=tenant.name,
                intake=intake,
                approved_by=approved_by,
                job_link=job_link,
            )
            html_body = cls._job_posting_approved_email_html(
                tenant_name=tenant.name,
                intake=intake,
                approved_by=approved_by,
                job_link=job_link,
            )

            message = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@levvai.com"),
                to=recipients,
            )
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
        except Exception:
            logger.exception(
                "intake_supplier_approval_notification_failed tenant_id=%s intake_id=%s supplier_id=%s",
                getattr(tenant, "id", None),
                intake.id,
                intake.supplier_id,
            )

    @staticmethod
    def _supplier_notification_recipients(*, tenant, intake):
        if not intake.supplier_id:
            return []

        recipients = set()
        if intake.supplier:
            if intake.supplier.email:
                recipients.add(intake.supplier.email.strip().lower())
            if intake.supplier.contact_email:
                recipients.add(intake.supplier.contact_email.strip().lower())

        from apps.accounts.models import Membership

        supplier_memberships = (
            Membership.objects.filter(
                tenant=tenant,
                role=Membership.ROLE_SUPPLIER,
                status=Membership.STATUS_ACTIVE,
                is_active=True,
                supplier_id=intake.supplier_id,
            )
            .select_related("user")
            .all()
        )
        for membership in supplier_memberships:
            if membership.user and membership.user.email:
                recipients.add(membership.user.email.strip().lower())

        return sorted(email for email in recipients if email)

    @staticmethod
    def _job_posting_link(*, intake, portal_base_url):
        configured_base_url = (getattr(settings, "APP_BASE_URL", "") or "").strip().rstrip("/")
        base_url = configured_base_url or (portal_base_url or "").strip().rstrip("/")
        if not base_url:
            return f"/my-items/job-postings/{intake.id}"
        return f"{base_url}/my-items/job-postings/{intake.id}"

    @staticmethod
    def _job_posting_approved_email_text(*, tenant_name, intake, approved_by, job_link):
        approved_by_name = approved_by.get_full_name().strip() or approved_by.username
        return (
            f"The job posting '{intake.title or f'Request #{intake.id}'}' has been fully approved in {tenant_name}.\n\n"
            f"Approved by: {approved_by_name}\n"
            f"Request ID: {intake.id}\n"
            f"Supplier: {intake.supplier.name if intake.supplier else 'N/A'}\n\n"
            f"Open job posting: {job_link}\n\n"
            "Next step: open the job posting and submit one or more candidates for buyer review."
        )

    @staticmethod
    def _job_posting_approved_email_html(*, tenant_name, intake, approved_by, job_link):
        approved_by_name = escape(approved_by.get_full_name().strip() or approved_by.username)
        title = escape(intake.title or f"Request #{intake.id}")
        supplier = escape(intake.supplier.name if intake.supplier else "N/A")
        link = escape(job_link)
        tenant_safe = escape(tenant_name)
        return f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.5;">
            <h2 style="margin: 0 0 12px;">Job Posting Approved</h2>
            <p>The job posting <strong>{title}</strong> has been fully approved in <strong>{tenant_safe}</strong>.</p>
            <p>
              <strong>Approved by:</strong> {approved_by_name}<br/>
              <strong>Request ID:</strong> {intake.id}<br/>
              <strong>Supplier:</strong> {supplier}
            </p>
            <p>
              <a href="{link}" style="display:inline-block;padding:10px 14px;background:#0b1f44;color:#fff;text-decoration:none;border-radius:6px;">
                Open Job Posting
              </a>
            </p>
            <p>Open the job posting and submit one or more candidates for buyer review.</p>
          </body>
        </html>
        """
