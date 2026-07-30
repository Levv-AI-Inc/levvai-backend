import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from html import escape
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Membership, User
from apps.audit.models import AuditEvent
from apps.policies.models import (
    WorkerLifecycleWorkflow,
    WorkflowBlock,
    WorkflowPolicyScopeField,
    WorkflowRequirement,
)
from apps.workers.models import (
    Engagement,
    LifecycleActivity,
    LifecycleBlock,
    LifecycleRun,
    Worker,
    WorkerEngagement,
    WorkerInvite,
)
from apps.workorders.models import WorkOrder


class LifecycleConfigurationError(Exception):
    pass


class LifecycleTransitionError(Exception):
    pass


class InviteDeliveryError(Exception):
    pass


@dataclass
class EngagementAcceptance:
    engagement: Engagement
    worker: Worker
    worker_engagement: WorkerEngagement
    onboarding_run: LifecycleRun
    worker_is_new: bool
    registration_required: bool
    registration_link: str
    invite: WorkerInvite | None


@dataclass
class WorkOrderAcceptance:
    work_order: WorkOrder
    worker: Worker
    worker_engagement: WorkerEngagement
    onboarding_run: LifecycleRun
    worker_is_new: bool
    registration_required: bool
    registration_link: str
    invite: WorkerInvite | None


def _normalize_text(value):
    return str(value or "").strip().casefold()


def _tenant_id_from(value):
    return getattr(value, "id", value)


class WorkflowMatcher:
    STATUS_PRIORITY = {
        WorkerLifecycleWorkflow.STATUS_PUBLISHED: 2,
        WorkerLifecycleWorkflow.STATUS_DRAFT: 1,
    }

    @classmethod
    def resolve(
        cls,
        *,
        work_order,
        lifecycle_type,
        worker_type=WorkerEngagement.WORKER_TYPE_CONTINGENT,
    ):
        workflow = cls.resolve_optional(
            work_order=work_order,
            lifecycle_type=lifecycle_type,
            worker_type=worker_type,
        )
        if workflow is None:
            raise LifecycleConfigurationError(
                f"No active {lifecycle_type} workflow matches this worker."
            )
        return workflow

    @classmethod
    def resolve_optional(cls, *, work_order, lifecycle_type, worker_type):
        tenant_id = work_order.tenant_id
        candidates = (
            WorkerLifecycleWorkflow.objects.filter(
                Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
                workflow_type=lifecycle_type,
                is_active=True,
            )
            .exclude(status=WorkerLifecycleWorkflow.STATUS_ARCHIVED)
            .select_related("policy_scope")
            .prefetch_related(
                "policy_scope__fields__location",
                "policy_scope__fields__cost_center",
                "policy_scope__fields__business_unit",
                "policy_scope__fields__role_definition",
                "policy_scope__fields__supplier",
                "blocks__requirements__requirement",
            )
        )

        matches = []
        for workflow in candidates:
            scope = getattr(workflow, "policy_scope", None)
            if not cls._scope_matches(
                scope=scope,
                work_order=work_order,
                worker_type=worker_type,
            ):
                continue
            field_count = scope.fields.count() if scope else 0
            worker_type_count = 1 if scope and scope.worker_type else 0
            matches.append(
                (
                    field_count + worker_type_count,
                    cls.STATUS_PRIORITY.get(workflow.status, 0),
                    workflow.version,
                    workflow.updated_at,
                    workflow.id,
                    workflow,
                )
            )

        if not matches:
            return None
        matches.sort(key=lambda item: item[:-1], reverse=True)
        return matches[0][-1]

    @classmethod
    def _scope_matches(cls, *, scope, work_order, worker_type):
        if scope is None:
            return True
        if scope.worker_type and scope.worker_type != worker_type:
            return False
        return all(
            cls._scope_field_matches(field=field, work_order=work_order)
            for field in scope.fields.all()
        )

    @classmethod
    def _scope_field_matches(cls, *, field, work_order):
        if field.field_key == WorkflowPolicyScopeField.FIELD_COST_CENTER:
            return field.cost_center_id == work_order.cost_center_id
        if field.field_key == WorkflowPolicyScopeField.FIELD_ROLE:
            return field.role_definition_id == work_order.role_definition_id
        if field.field_key == WorkflowPolicyScopeField.FIELD_SUPPLIER:
            return field.supplier_id == work_order.supplier_id
        if field.field_key == WorkflowPolicyScopeField.FIELD_BUSINESS_UNIT:
            business_unit = getattr(work_order.cost_center, "business_unit", None)
            return bool(
                business_unit
                and field.business_unit_id == business_unit.pk
            )
        if field.field_key == WorkflowPolicyScopeField.FIELD_LOCATION:
            return cls._location_matches(
                location=field.location,
                work_order=work_order,
            )
        return False

    @staticmethod
    def _location_matches(*, location, work_order):
        if not location:
            return False
        site = work_order.site
        location_name = _normalize_text(location.name)
        possible_names = {
            _normalize_text(work_order.work_location_label),
            _normalize_text(getattr(site, "name", "")),
            _normalize_text(getattr(site, "city", "")),
        }
        possible_names.discard("")
        if location_name in possible_names:
            return True
        return any(
            location_name and location_name in candidate
            for candidate in possible_names
        )


class LifecycleService:
    COMPLETE_ACTIVITY_STATUSES = {
        LifecycleActivity.STATUS_COMPLETE,
        LifecycleActivity.STATUS_WAIVED,
    }

    @classmethod
    def create_onboarding_run(cls, *, worker_engagement):
        return cls._create_run(
            worker_engagement=worker_engagement,
            lifecycle_type=LifecycleRun.TYPE_ONBOARDING,
        )

    @classmethod
    def start_offboarding(cls, *, worker_engagement):
        return cls._create_run(
            worker_engagement=worker_engagement,
            lifecycle_type=LifecycleRun.TYPE_OFFBOARDING,
        )

    @classmethod
    def _create_run(cls, *, worker_engagement, lifecycle_type):
        existing = (
            LifecycleRun.objects.filter(
                worker_engagement=worker_engagement,
                lifecycle_type=lifecycle_type,
            )
            .prefetch_related("blocks__activities")
            .first()
        )
        if existing:
            return existing, False

        work_order = worker_engagement.resolved_work_order
        if work_order is None:
            raise LifecycleConfigurationError(
                "Worker assignment is not connected to a work order."
            )
        workflow = WorkflowMatcher.resolve_optional(
            work_order=work_order,
            lifecycle_type=lifecycle_type,
            worker_type=worker_engagement.worker_type,
        )
        derived = False
        if workflow is None and lifecycle_type == LifecycleRun.TYPE_OFFBOARDING:
            workflow = WorkflowMatcher.resolve_optional(
                work_order=work_order,
                lifecycle_type=LifecycleRun.TYPE_ONBOARDING,
                worker_type=worker_engagement.worker_type,
            )
            derived = workflow is not None
        if workflow is None:
            raise LifecycleConfigurationError(
                f"No active {lifecycle_type} workflow matches this worker."
            )

        with transaction.atomic():
            run = LifecycleRun(
                tenant_id=worker_engagement.tenant_id,
                worker_engagement=worker_engagement,
                lifecycle_type=lifecycle_type,
                workflow=workflow,
                workflow_name=(
                    f"{workflow.name} (Derived Offboarding)"
                    if derived
                    else workflow.name
                ),
                workflow_version=workflow.version,
                status=LifecycleRun.STATUS_PENDING,
                snapshot=cls._workflow_snapshot(
                    workflow=workflow,
                    lifecycle_type=lifecycle_type,
                    derived=derived,
                ),
            )
            run.full_clean()
            run.save()
            cls._create_block_instances(
                run=run,
                workflow=workflow,
                derived=derived,
            )
            cls.sync_run(run)
        return run, True

    @classmethod
    def _workflow_snapshot(cls, *, workflow, lifecycle_type, derived):
        scope = getattr(workflow, "policy_scope", None)
        scope_fields = []
        if scope:
            for field in scope.fields.all():
                target = field.target()
                scope_fields.append(
                    {
                        "field_key": field.field_key,
                        "operator": field.operator,
                        "target_id": getattr(target, "pk", None),
                        "display": str(target) if target is not None else "",
                    }
                )

        dependencies = list(workflow.dependencies or [])
        if derived:
            dependencies = cls._reverse_dependencies(dependencies)

        return {
            "source_workflow_id": workflow.id,
            "source_workflow_type": workflow.workflow_type,
            "lifecycle_type": lifecycle_type,
            "derived": derived,
            "dependencies": dependencies,
            "policy_scope": {
                "worker_type": scope.worker_type if scope else "",
                "fields": scope_fields,
            },
        }

    @staticmethod
    def _reverse_dependencies(dependencies):
        reversed_dependencies = []
        for dependency in dependencies or []:
            from_key = dependency.get("from_block_key")
            to_key = dependency.get("to_block_key")
            if not from_key or not to_key:
                continue
            if to_key == "__end__":
                next_from = "__start__"
            else:
                next_from = to_key
            if from_key == "__start__":
                next_to = "__end__"
            else:
                next_to = from_key
            reversed_dependencies.append(
                {
                    "from_block_key": next_from,
                    "to_block_key": next_to,
                }
            )
        return reversed_dependencies

    @classmethod
    def _create_block_instances(cls, *, run, workflow, derived):
        source_blocks = list(
            workflow.blocks.all().prefetch_related("requirements__requirement")
        )
        if derived:
            source_blocks.reverse()

        for sequence, source_block in enumerate(source_blocks, start=1):
            client_key = source_block.client_key or f"block-{source_block.id}"
            config = dict(source_block.config or {})
            name = source_block.name
            if derived:
                unwind = config.get("system_unwind")
                if isinstance(unwind, dict) and unwind.get("action"):
                    name = str(unwind["action"])
                else:
                    name = f"Offboard: {source_block.name}"

            block = LifecycleBlock.objects.create(
                run=run,
                source_block_id=source_block.id,
                client_key=client_key,
                sequence=sequence,
                block_type=source_block.block_type,
                name=name,
                gate_type=source_block.gate_type,
                integration_type=source_block.integration_type,
                status=LifecycleBlock.STATUS_GATED,
                config=config,
                layout=dict(source_block.layout or {}),
            )
            cls._create_activity_instances(
                block=block,
                source_block=source_block,
                derived=derived,
            )

    @classmethod
    def _create_activity_instances(cls, *, block, source_block, derived):
        requirements = list(source_block.requirements.all())
        if derived:
            requirements.reverse()

        if source_block.block_type == WorkflowBlock.TYPE_SYSTEM:
            owner = WorkflowRequirement.OWNER_SYSTEM
            activity_name = block.name
            LifecycleActivity.objects.create(
                block=block,
                sequence=1,
                name=activity_name,
                owner=owner,
                config={
                    "integration_type": source_block.integration_type,
                    **dict(source_block.config or {}),
                },
            )
            return

        for sequence, source_requirement in enumerate(requirements, start=1):
            config = dict(source_requirement.config or {})
            name = source_requirement.name
            if derived:
                unwind = config.get("unwind")
                if isinstance(unwind, dict) and unwind.get("action"):
                    name = str(unwind["action"])
                else:
                    name = f"Reverse: {source_requirement.name}"
            LifecycleActivity.objects.create(
                block=block,
                source_requirement_id=source_requirement.requirement_id,
                sequence=sequence,
                name=name,
                owner=source_requirement.owner,
                config=config,
            )

    @classmethod
    def update_activity(
        cls,
        *,
        activity,
        user,
        activity_status,
        evidence=None,
        notes=None,
    ):
        allowed_statuses = {
            choice[0] for choice in LifecycleActivity.STATUS_CHOICES
        }
        if activity_status not in allowed_statuses:
            raise LifecycleTransitionError("Unsupported activity status.")
        if (
            activity.block.status == LifecycleBlock.STATUS_GATED
            and activity_status
            in {
                LifecycleActivity.STATUS_COMPLETE,
                LifecycleActivity.STATUS_WAIVED,
            }
        ):
            raise LifecycleTransitionError(
                "This activity is gated by an incomplete hard dependency."
            )
        if evidence is not None and not isinstance(evidence, dict):
            raise LifecycleTransitionError("Evidence must be an object.")

        now = timezone.now()
        with transaction.atomic():
            activity = LifecycleActivity.objects.select_for_update().get(
                pk=activity.pk
            )
            activity.status = activity_status
            if evidence is not None:
                activity.evidence = evidence
            if notes is not None:
                activity.notes = str(notes).strip()
            if activity_status == LifecycleActivity.STATUS_IN_PROGRESS:
                activity.started_at = activity.started_at or now
                activity.completed_at = None
                activity.completed_by = None
            elif activity_status in cls.COMPLETE_ACTIVITY_STATUSES:
                activity.started_at = activity.started_at or now
                activity.completed_at = now
                activity.completed_by = user
            else:
                activity.completed_at = None
                activity.completed_by = None
            activity.full_clean()
            activity.save()
            cls.sync_run(activity.block.run)
        return activity

    @classmethod
    def sync_run(cls, run):
        run = LifecycleRun.objects.select_related(
            "worker_engagement__worker",
            "worker_engagement__work_order",
            "worker_engagement__engagement__work_order",
        ).get(pk=run.pk)
        blocks = list(
            run.blocks.all().prefetch_related("activities").order_by("sequence")
        )
        now = timezone.now()

        for block in blocks:
            activities = list(block.activities.all())
            completed_count = sum(
                1
                for activity in activities
                if activity.status in cls.COMPLETE_ACTIVITY_STATUSES
            )
            blocked = any(
                activity.status == LifecycleActivity.STATUS_BLOCKED
                for activity in activities
            )
            completion_rule = str(
                (block.config or {}).get("completion_rule", "ALL")
            ).upper()
            completion_n = int(
                (block.config or {}).get("completion_n") or len(activities) or 1
            )
            complete = False
            if activities:
                if completion_rule == "ANY":
                    complete = completed_count >= 1
                elif completion_rule == "N_OF":
                    complete = completed_count >= completion_n
                else:
                    complete = completed_count == len(activities)

            if blocked:
                next_status = LifecycleBlock.STATUS_BLOCKED
            elif complete:
                next_status = LifecycleBlock.STATUS_COMPLETE
            elif cls._block_is_unlocked(block=block, blocks=blocks, run=run):
                next_status = LifecycleBlock.STATUS_IN_PROGRESS
            else:
                next_status = LifecycleBlock.STATUS_GATED

            update_fields = []
            if block.status != next_status:
                block.status = next_status
                update_fields.append("status")
            if next_status == LifecycleBlock.STATUS_IN_PROGRESS and not block.started_at:
                block.started_at = now
                update_fields.append("started_at")
            if next_status == LifecycleBlock.STATUS_COMPLETE and not block.completed_at:
                block.completed_at = now
                update_fields.append("completed_at")
            elif next_status != LifecycleBlock.STATUS_COMPLETE and block.completed_at:
                block.completed_at = None
                update_fields.append("completed_at")
            if update_fields:
                update_fields.append("updated_at")
                block.save(update_fields=update_fields)

        statuses = {block.status for block in blocks}
        if blocks and statuses <= {
            LifecycleBlock.STATUS_COMPLETE,
            LifecycleBlock.STATUS_SKIPPED,
        }:
            next_run_status = LifecycleRun.STATUS_COMPLETE
        elif LifecycleBlock.STATUS_BLOCKED in statuses:
            next_run_status = LifecycleRun.STATUS_BLOCKED
        elif LifecycleBlock.STATUS_IN_PROGRESS in statuses:
            next_run_status = LifecycleRun.STATUS_IN_PROGRESS
        else:
            next_run_status = LifecycleRun.STATUS_PENDING

        run_update_fields = []
        if run.status != next_run_status:
            run.status = next_run_status
            run_update_fields.append("status")
        if next_run_status == LifecycleRun.STATUS_COMPLETE and not run.completed_at:
            run.completed_at = now
            run_update_fields.append("completed_at")
        elif next_run_status != LifecycleRun.STATUS_COMPLETE and run.completed_at:
            run.completed_at = None
            run_update_fields.append("completed_at")
        if run_update_fields:
            run_update_fields.append("updated_at")
            run.save(update_fields=run_update_fields)

        cls._sync_assignment_status(run)
        return run

    @classmethod
    def _block_is_unlocked(cls, *, block, blocks, run):
        dependencies = (run.snapshot or {}).get("dependencies") or []
        by_key = {candidate.client_key: candidate for candidate in blocks}
        incoming = [
            dependency.get("from_block_key")
            for dependency in dependencies
            if dependency.get("to_block_key") == block.client_key
        ]
        if dependencies:
            hard_predecessors = [
                by_key[key]
                for key in incoming
                if key in by_key
                and by_key[key].gate_type == WorkflowBlock.GATE_HARD
            ]
            return all(
                predecessor.status
                in {
                    LifecycleBlock.STATUS_COMPLETE,
                    LifecycleBlock.STATUS_SKIPPED,
                }
                for predecessor in hard_predecessors
            )

        earlier_hard_blocks = [
            candidate
            for candidate in blocks
            if candidate.sequence < block.sequence
            and candidate.gate_type == WorkflowBlock.GATE_HARD
        ]
        return all(
            predecessor.status
            in {
                LifecycleBlock.STATUS_COMPLETE,
                LifecycleBlock.STATUS_SKIPPED,
            }
            for predecessor in earlier_hard_blocks
        )

    @classmethod
    def _sync_assignment_status(cls, run):
        assignment = run.worker_engagement
        worker = assignment.worker
        work_order = assignment.resolved_work_order
        if work_order is None:
            raise LifecycleTransitionError(
                "Worker assignment is not connected to a work order."
            )

        if run.lifecycle_type == LifecycleRun.TYPE_ONBOARDING:
            if run.status == LifecycleRun.STATUS_COMPLETE:
                assignment.status = WorkerEngagement.STATUS_ACTIVE
                work_order.status = WorkOrder.STATUS_ACTIVE
            else:
                assignment.status = WorkerEngagement.STATUS_ONBOARDING
        elif run.status == LifecycleRun.STATUS_COMPLETE:
            assignment.status = WorkerEngagement.STATUS_COMPLETE
            work_order.status = WorkOrder.STATUS_CLOSED
        else:
            assignment.status = WorkerEngagement.STATUS_OFFBOARDING

        assignment.save(update_fields=["status", "updated_at"])
        worker.status = cls._worker_status_from_assignments(worker)
        worker.save(update_fields=["status", "updated_at"])
        work_order.save(update_fields=["status", "updated_at"])

    @staticmethod
    def _worker_status_from_assignments(worker):
        statuses = set(
            WorkerEngagement.objects.filter(worker=worker).values_list(
                "status",
                flat=True,
            )
        )
        if WorkerEngagement.STATUS_ACTIVE in statuses:
            return Worker.STATUS_ACTIVE
        if WorkerEngagement.STATUS_ONBOARDING in statuses:
            return Worker.STATUS_ONBOARDING
        if WorkerEngagement.STATUS_OFFBOARDING in statuses:
            return Worker.STATUS_OFFBOARDING
        return Worker.STATUS_OFFBOARDED


class WorkerInviteService:
    @classmethod
    def link_existing_tenant_user(cls, *, worker, tenant):
        user = User.objects.filter(
            Q(email__iexact=worker.email) | Q(username__iexact=worker.email)
        ).first()
        if not user:
            return None
        membership = Membership.objects.filter(
            user=user,
            tenant=tenant,
        ).first()
        if not membership:
            return None
        if membership.role != Membership.ROLE_WORKER:
            raise LifecycleConfigurationError(
                "The worker email belongs to a non-worker account in this tenant."
            )
        if (
            membership.status != Membership.STATUS_ACTIVE
            or not membership.is_active
        ):
            return None
        worker.user = user
        worker.registered_at = worker.registered_at or timezone.now()
        worker.status = Worker.STATUS_ONBOARDING
        worker.save(
            update_fields=["user", "registered_at", "status", "updated_at"]
        )
        return user

    @classmethod
    def issue(
        cls,
        *,
        worker,
        work_order=None,
        engagement=None,
        invited_by,
        base_url,
        send_email=True,
    ):
        if work_order is None and engagement is not None:
            work_order = engagement.work_order
        if work_order is None:
            raise LifecycleConfigurationError(
                "A work order is required to invite a worker."
            )
        now = timezone.now()
        WorkerInvite.objects.filter(
            worker=worker,
            status=WorkerInvite.STATUS_PENDING,
            expires_at__lte=now,
        ).update(status=WorkerInvite.STATUS_EXPIRED, updated_at=now)
        invite = (
            WorkerInvite.objects.filter(
                worker=worker,
                work_order=work_order,
                status=WorkerInvite.STATUS_PENDING,
                expires_at__gt=now,
            )
            .order_by("-created_at")
            .first()
        )
        if invite is None:
            invite = WorkerInvite(
                worker=worker,
                engagement=engagement,
                work_order=work_order,
                email=worker.email,
                invited_by=invited_by,
                expires_at=now + timedelta(days=7),
            )
            invite.full_clean()
            invite.save()

        registration_link = cls.build_registration_link(
            base_url=base_url,
            invite=invite,
        )
        if send_email:
            cls.send(invite=invite, registration_link=registration_link)
        return invite, registration_link

    @staticmethod
    def build_registration_link(*, base_url, invite):
        next_path = (
            f"/workers/{invite.worker_id}/engagements/onboarding/workspace"
        )
        if invite.work_order_id:
            next_path = (
                f"{next_path}?work_order={invite.work_order_id}"
            )
        elif invite.engagement_id:
            next_path = (
                f"{next_path}?engagement={invite.engagement_id}"
            )
        query = urlencode(
            {
                "mode": "register",
                "worker_invite_token": invite.token,
                "email": invite.email,
                "next": next_path,
            }
        )
        return f"{base_url.rstrip('/')}/auth/login?{query}"

    @staticmethod
    def send(*, invite, registration_link):
        expires_text = timezone.localtime(invite.expires_at).strftime(
            "%Y-%m-%d %H:%M %Z"
        )
        worker_name = invite.worker.full_name
        subject = "Complete your worker registration on LEVV"
        text_body = (
            f"Hi {worker_name},\n\n"
            "Your work order has been accepted and your onboarding is ready.\n"
            f"Create your account using this link:\n{registration_link}\n\n"
            f"This invite expires on {expires_text}."
        )
        link_safe = escape(registration_link, quote=True)
        html_body = (
            "<!doctype html><html><body style=\"font-family:Arial,sans-serif;"
            "color:#0f172a;background:#f4f7fb;padding:32px\">"
            "<div style=\"max-width:620px;margin:auto;background:#fff;"
            "border:1px solid #e2e8f0;padding:28px\">"
            f"<h2 style=\"margin-top:0\">Welcome to LEVV, {escape(worker_name)}</h2>"
            "<p>Your work order has been accepted and your onboarding is ready.</p>"
            f"<p><a href=\"{link_safe}\" style=\"display:inline-block;"
            "background:#020617;color:#fff;text-decoration:none;padding:12px 18px;"
            "border-radius:6px\">Complete registration</a></p>"
            f"<p style=\"font-size:12px;color:#64748b\">Expires {escape(expires_text)}</p>"
            "</div></body></html>"
        )
        try:
            message = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=settings.WORKER_INVITE_FROM_EMAIL,
                to=[invite.email],
            )
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
        except Exception as exc:
            raise InviteDeliveryError(
                "Worker registration email could not be sent."
            ) from exc


class WorkerContractService:
    @classmethod
    def extend(
        cls,
        *,
        tenant,
        user,
        worker_engagement,
        end_date,
        notes="",
    ):
        with transaction.atomic():
            assignment = (
                WorkerEngagement.objects.select_for_update(of=("self",))
                .select_related("engagement")
                .get(pk=worker_engagement.pk)
            )
            if assignment.status not in {
                WorkerEngagement.STATUS_ONBOARDING,
                WorkerEngagement.STATUS_ACTIVE,
            }:
                raise LifecycleTransitionError(
                    "Only onboarding or active assignments can be extended."
                )
            work_order = WorkOrder.objects.select_for_update().get(
                pk=assignment.resolved_work_order.id
            )
            previous_end_date = work_order.end_date
            if previous_end_date and end_date <= previous_end_date:
                raise LifecycleTransitionError(
                    "The new contract end date must be later than the current end date."
                )
            if work_order.start_date and end_date < work_order.start_date:
                raise LifecycleTransitionError(
                    "The contract end date cannot be before the start date."
                )
            if end_date <= timezone.localdate():
                raise LifecycleTransitionError(
                    "The extended contract end date must be in the future."
                )

            work_order.end_date = end_date
            work_order.full_clean()
            work_order.save(update_fields=["end_date", "updated_at"])

            snapshot = dict(work_order.source_snapshot or {})
            extensions = list(snapshot.get("contract_extensions") or [])
            extensions.append(
                {
                    "previous_end_date": (
                        previous_end_date.isoformat()
                        if previous_end_date
                        else None
                    ),
                    "new_end_date": end_date.isoformat(),
                    "notes": (notes or "").strip(),
                    "extended_by": user.id,
                    "extended_at": timezone.now().isoformat(),
                }
            )
            snapshot["contract_extensions"] = extensions
            work_order.source_snapshot = snapshot
            work_order.save(update_fields=["source_snapshot", "updated_at"])

        cls._audit(
            tenant=tenant,
            user=user,
            assignment=assignment,
            previous_end_date=previous_end_date,
            new_end_date=end_date,
        )
        return assignment

    @staticmethod
    def _audit(
        *,
        tenant,
        user,
        assignment,
        previous_end_date,
        new_end_date,
    ):
        payload = {
            "worker_id": assignment.worker_id,
            "worker_assignment_id": assignment.id,
            "work_order_id": assignment.resolved_work_order.id,
            "previous_end_date": previous_end_date,
            "new_end_date": new_end_date,
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
            action="worker.contract_extended",
            object_type="worker_assignment",
            object_id=str(assignment.id),
            payload_hash=payload_hash,
        )


class WorkOrderSupplierService:
    @classmethod
    def accept(
        cls,
        *,
        tenant,
        user,
        work_order,
        supplier_response_notes="",
        base_url,
        send_email=True,
    ):
        with transaction.atomic():
            work_order = (
                WorkOrder.objects.select_for_update(of=("self",))
                .select_related(
                    "supplier",
                    "role_definition",
                    "cost_center__business_unit",
                    "site",
                )
                .get(pk=work_order.pk)
            )
            if (
                work_order.status != WorkOrder.STATUS_APPROVED
                or work_order.supplier_acceptance_status
                != WorkOrder.SUPPLIER_ACCEPTANCE_PENDING
            ):
                raise LifecycleTransitionError(
                    "Only approved work orders pending supplier acceptance can be accepted."
                )

            email = (work_order.worker_email or "").strip().lower()
            full_name = (work_order.worker_full_name or "").strip()
            if not email:
                raise LifecycleConfigurationError(
                    "The work order must include the worker email before acceptance."
                )
            if not full_name:
                raise LifecycleConfigurationError(
                    "The work order must include the worker name before acceptance."
                )

            tenant_id = _tenant_id_from(tenant)
            worker = Worker.objects.filter(
                tenant_id=tenant_id,
                email__iexact=email,
            ).first()
            worker_profile_created = worker is None
            if worker is None:
                worker = Worker(
                    tenant_id=tenant_id,
                    email=email,
                    full_name=full_name,
                    phone=work_order.worker_phone,
                    status=Worker.STATUS_INVITED,
                )
                worker.full_clean()
                worker.save()
            else:
                changed_fields = []
                if not worker.full_name and full_name:
                    worker.full_name = full_name
                    changed_fields.append("full_name")
                if not worker.phone and work_order.worker_phone:
                    worker.phone = work_order.worker_phone
                    changed_fields.append("phone")
                if changed_fields:
                    changed_fields.append("updated_at")
                    worker.save(update_fields=changed_fields)

            existing_user = WorkerInviteService.link_existing_tenant_user(
                worker=worker,
                tenant=tenant,
            )
            worker_is_new = (
                worker_profile_created and existing_user is None
            )
            worker_engagement, _ = WorkerEngagement.objects.get_or_create(
                work_order=work_order,
                defaults={
                    "tenant_id": tenant_id,
                    "worker": worker,
                    "worker_type": WorkerEngagement.WORKER_TYPE_CONTINGENT,
                    "status": WorkerEngagement.STATUS_ONBOARDING,
                },
            )
            if worker_engagement.worker_id != worker.id:
                raise LifecycleTransitionError(
                    "This work order is already linked to another worker."
                )

            onboarding_run, _ = LifecycleService.create_onboarding_run(
                worker_engagement=worker_engagement,
            )
            invite = None
            registration_link = ""
            if existing_user is None and worker.user_id is None:
                invite, registration_link = WorkerInviteService.issue(
                    worker=worker,
                    work_order=work_order,
                    invited_by=user,
                    base_url=base_url,
                    send_email=send_email,
                )

            work_order.supplier_acceptance_status = (
                WorkOrder.SUPPLIER_ACCEPTANCE_ACCEPTED
            )
            work_order.supplier_accepted_at = timezone.now()
            work_order.supplier_accepted_by = user
            work_order.supplier_response_notes = (
                supplier_response_notes or ""
            ).strip()
            work_order.source_snapshot = {
                **dict(work_order.source_snapshot or {}),
                "worker_runtime": {
                    "worker_id": worker.id,
                    "worker_is_new": worker_is_new,
                    "worker_assignment_id": worker_engagement.id,
                    "onboarding_run_id": onboarding_run.id,
                    "matched_workflow_id": onboarding_run.workflow_id,
                    "registration_required": bool(invite),
                },
            }
            work_order.save(
                update_fields=[
                    "supplier_acceptance_status",
                    "supplier_accepted_at",
                    "supplier_accepted_by",
                    "supplier_response_notes",
                    "source_snapshot",
                    "updated_at",
                ]
            )

        cls._audit(
            tenant=tenant,
            user=user,
            action="work_order.supplier_accepted",
            work_order=work_order,
        )
        return WorkOrderAcceptance(
            work_order=work_order,
            worker=worker,
            worker_engagement=worker_engagement,
            onboarding_run=onboarding_run,
            worker_is_new=worker_is_new,
            registration_required=bool(invite),
            registration_link=registration_link,
            invite=invite,
        )

    @classmethod
    def request_change(cls, *, tenant, user, work_order, notes):
        notes = (notes or "").strip()
        if not notes:
            raise LifecycleTransitionError(
                "Supplier response notes are required when requesting changes."
            )
        with transaction.atomic():
            work_order = WorkOrder.objects.select_for_update().get(
                pk=work_order.pk
            )
            if (
                work_order.status != WorkOrder.STATUS_APPROVED
                or work_order.supplier_acceptance_status
                != WorkOrder.SUPPLIER_ACCEPTANCE_PENDING
            ):
                raise LifecycleTransitionError(
                    "Only approved work orders pending supplier acceptance can request changes."
                )
            work_order.supplier_acceptance_status = (
                WorkOrder.SUPPLIER_ACCEPTANCE_CHANGES_REQUESTED
            )
            work_order.supplier_change_requested_at = timezone.now()
            work_order.supplier_change_requested_by = user
            work_order.supplier_response_notes = notes
            work_order.save(
                update_fields=[
                    "supplier_acceptance_status",
                    "supplier_change_requested_at",
                    "supplier_change_requested_by",
                    "supplier_response_notes",
                    "updated_at",
                ]
            )
        cls._audit(
            tenant=tenant,
            user=user,
            action="work_order.supplier_changes_requested",
            work_order=work_order,
        )
        return work_order

    @staticmethod
    def _audit(*, tenant, user, action, work_order):
        payload = {
            "work_order_id": work_order.id,
            "work_order_number": work_order.work_order_number,
            "supplier_acceptance_status": (
                work_order.supplier_acceptance_status
            ),
        }
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=action,
            object_type="work_order",
            object_id=str(work_order.id),
            payload_hash=hashlib.sha256(
                json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
        )


class EngagementService:
    @classmethod
    def ensure_for_approved_work_order(cls, *, tenant, user, work_order):
        if work_order.status not in {
            WorkOrder.STATUS_APPROVED,
            WorkOrder.STATUS_ACTIVE,
        }:
            raise LifecycleTransitionError(
                "An engagement can only be created for an approved work order."
            )
        engagement, created = Engagement.objects.get_or_create(
            work_order=work_order,
            defaults={
                "tenant_id": _tenant_id_from(tenant),
                "status": Engagement.STATUS_PENDING_SUPPLIER_ACCEPTANCE,
                "source_snapshot": dict(work_order.source_snapshot or {}),
                "created_by": user,
            },
        )
        if created:
            engagement.engagement_number = (
                f"ENG-{timezone.now().year}-{engagement.id:05d}"
            )
            engagement.full_clean()
            engagement.save(
                update_fields=["engagement_number", "updated_at"]
            )
            cls._audit(
                tenant=tenant,
                user=user,
                action="engagement.created",
                engagement=engagement,
            )
        return engagement

    @classmethod
    def accept(
        cls,
        *,
        tenant,
        user,
        engagement,
        supplier_response_notes="",
        base_url,
        send_email=True,
    ):
        with transaction.atomic():
            engagement = (
                Engagement.objects.select_for_update(of=("self",))
                .select_related(
                    "work_order__supplier",
                    "work_order__role_definition",
                    "work_order__cost_center__business_unit",
                    "work_order__site",
                )
                .get(pk=engagement.pk)
            )
            if (
                engagement.status
                != Engagement.STATUS_PENDING_SUPPLIER_ACCEPTANCE
            ):
                raise LifecycleTransitionError(
                    "Only engagements pending supplier acceptance can be accepted."
                )

            work_order = engagement.work_order
            email = (work_order.worker_email or "").strip().lower()
            full_name = (work_order.worker_full_name or "").strip()
            if not email:
                raise LifecycleConfigurationError(
                    "The work order must include the worker email before acceptance."
                )
            if not full_name:
                raise LifecycleConfigurationError(
                    "The work order must include the worker name before acceptance."
                )

            worker = Worker.objects.filter(
                tenant_id=_tenant_id_from(tenant),
                email__iexact=email,
            ).first()
            worker_is_new = worker is None
            if worker is None:
                worker = Worker(
                    tenant_id=_tenant_id_from(tenant),
                    email=email,
                    full_name=full_name,
                    phone=work_order.worker_phone,
                    status=Worker.STATUS_INVITED,
                )
                worker.full_clean()
                worker.save()
            else:
                changed_fields = []
                if not worker.full_name and full_name:
                    worker.full_name = full_name
                    changed_fields.append("full_name")
                if not worker.phone and work_order.worker_phone:
                    worker.phone = work_order.worker_phone
                    changed_fields.append("phone")
                if changed_fields:
                    changed_fields.append("updated_at")
                    worker.save(update_fields=changed_fields)

            existing_user = WorkerInviteService.link_existing_tenant_user(
                worker=worker,
                tenant=tenant,
            )
            worker_engagement, _ = WorkerEngagement.objects.get_or_create(
                engagement=engagement,
                defaults={
                    "tenant_id": _tenant_id_from(tenant),
                    "worker": worker,
                    "worker_type": WorkerEngagement.WORKER_TYPE_CONTINGENT,
                    "status": WorkerEngagement.STATUS_ONBOARDING,
                },
            )
            if worker_engagement.worker_id != worker.id:
                raise LifecycleTransitionError(
                    "This engagement is already linked to another worker."
                )

            onboarding_run, _ = LifecycleService.create_onboarding_run(
                worker_engagement=worker_engagement,
            )
            invite = None
            registration_link = ""
            if existing_user is None and worker.user_id is None:
                invite, registration_link = WorkerInviteService.issue(
                    worker=worker,
                    engagement=engagement,
                    invited_by=user,
                    base_url=base_url,
                    send_email=send_email,
                )

            engagement.status = Engagement.STATUS_ACCEPTED
            engagement.accepted_at = timezone.now()
            engagement.accepted_by = user
            engagement.supplier_response_notes = (
                supplier_response_notes or ""
            ).strip()
            engagement.source_snapshot = {
                **dict(engagement.source_snapshot or {}),
                "worker_runtime": {
                    "worker_id": worker.id,
                    "worker_is_new": worker_is_new,
                    "worker_engagement_id": worker_engagement.id,
                    "onboarding_run_id": onboarding_run.id,
                    "matched_workflow_id": onboarding_run.workflow_id,
                    "registration_required": bool(invite),
                },
            }
            engagement.full_clean()
            engagement.save()

            if work_order.status == WorkOrder.STATUS_APPROVED:
                work_order.status = WorkOrder.STATUS_ACTIVE
                work_order.save(update_fields=["status", "updated_at"])

        cls._audit(
            tenant=tenant,
            user=user,
            action="engagement.accepted",
            engagement=engagement,
        )
        return EngagementAcceptance(
            engagement=engagement,
            worker=worker,
            worker_engagement=worker_engagement,
            onboarding_run=onboarding_run,
            worker_is_new=worker_is_new,
            registration_required=bool(invite),
            registration_link=registration_link,
            invite=invite,
        )

    @classmethod
    def request_change(cls, *, tenant, user, engagement, notes):
        notes = (notes or "").strip()
        if not notes:
            raise LifecycleTransitionError(
                "Supplier response notes are required when requesting changes."
            )
        with transaction.atomic():
            engagement = Engagement.objects.select_for_update().get(
                pk=engagement.pk
            )
            if (
                engagement.status
                != Engagement.STATUS_PENDING_SUPPLIER_ACCEPTANCE
            ):
                raise LifecycleTransitionError(
                    "Only pending engagements can request changes."
                )
            engagement.status = Engagement.STATUS_CHANGES_REQUESTED
            engagement.change_requested_at = timezone.now()
            engagement.change_requested_by = user
            engagement.supplier_response_notes = notes
            engagement.save(
                update_fields=[
                    "status",
                    "change_requested_at",
                    "change_requested_by",
                    "supplier_response_notes",
                    "updated_at",
                ]
            )
        cls._audit(
            tenant=tenant,
            user=user,
            action="engagement.changes_requested",
            engagement=engagement,
        )
        return engagement

    @staticmethod
    def _audit(*, tenant, user, action, engagement):
        payload = {
            "id": engagement.id,
            "engagement_number": engagement.engagement_number,
            "work_order_id": engagement.work_order_id,
            "status": engagement.status,
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
            action=action,
            object_type="engagement",
            object_id=str(engagement.id),
            payload_hash=payload_hash,
        )
