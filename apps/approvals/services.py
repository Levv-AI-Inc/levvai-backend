import hashlib
import json

from django.db import transaction

from apps.approvals.models import ApprovalChain, ApprovalChainCondition, ApprovalChainStep
from apps.audit.models import AuditEvent


class ApprovalChainService:
    @classmethod
    def create_chain(cls, *, tenant, user, attrs, conditions, steps):
        with transaction.atomic():
            chain = ApprovalChain(**attrs)
            chain.full_clean()
            chain.save()
            cls._replace_conditions(chain=chain, conditions=conditions or [])
            cls._replace_steps(chain=chain, steps=steps or [])
            cls._audit(tenant=tenant, user=user, action="approval_chain.created", chain=chain)
            return chain

    @classmethod
    def update_chain(cls, *, tenant, user, chain, attrs, conditions=None, steps=None):
        with transaction.atomic():
            chain = ApprovalChain.objects.select_for_update().get(pk=chain.pk)
            for key, value in attrs.items():
                setattr(chain, key, value)
            chain.full_clean()
            chain.save()

            if conditions is not None:
                cls._replace_conditions(chain=chain, conditions=conditions)
            if steps is not None:
                cls._replace_steps(chain=chain, steps=steps)

            cls._audit(tenant=tenant, user=user, action="approval_chain.updated", chain=chain)
            return chain

    @classmethod
    def delete_chain(cls, *, tenant, user, chain):
        cls._audit(tenant=tenant, user=user, action="approval_chain.deleted", chain=chain)
        chain.delete()

    @classmethod
    def _replace_conditions(cls, *, chain, conditions):
        chain.conditions.all().delete()
        objects = [
            ApprovalChainCondition(approval_chain=chain, **item)
            for item in conditions
        ]
        for condition in objects:
            condition.full_clean()
        if objects:
            ApprovalChainCondition.objects.bulk_create(objects)

    @classmethod
    def _replace_steps(cls, *, chain, steps):
        chain.steps.all().delete()
        objects = [
            ApprovalChainStep(approval_chain=chain, **item)
            for item in steps
        ]
        for step in objects:
            step.full_clean()
        if objects:
            ApprovalChainStep.objects.bulk_create(objects)

    @classmethod
    def _snapshot_payload(cls, chain):
        return {
            "id": chain.id,
            "name": chain.name,
            "description": chain.description,
            "is_active": chain.is_active,
            "priority": chain.priority,
            "match_strategy": chain.match_strategy,
            "conditions": [
                {
                    "id": condition.id,
                    "sequence": condition.sequence,
                    "field_key": condition.field_key,
                    "operator": condition.operator,
                    "value": condition.value_json,
                }
                for condition in chain.conditions.all().order_by("sequence", "id")
            ],
            "steps": [
                {
                    "id": step.id,
                    "sequence": step.sequence,
                    "step_type": step.step_type,
                    "approver_id": step.approver_id,
                    "amount": str(step.amount),
                    "currency": step.currency,
                }
                for step in chain.steps.all().order_by("sequence", "id")
            ],
        }

    @classmethod
    def _audit(cls, *, tenant, user, action, chain):
        payload_hash = hashlib.sha256(
            json.dumps(cls._snapshot_payload(chain), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=action,
            object_type="approval_chain",
            object_id=str(chain.id),
            payload_hash=payload_hash,
        )

