import hashlib
import json

from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.rates.calculations import calculate_bill_rate_for_structure
from apps.rates.models import (
    RateCard,
    RateCardLine,
    RateCardLineValue,
    RateRule,
    RateRuleCondition,
    RateStructure,
    RateStructureComponent,
)


class RateStructureService:
    @classmethod
    def create_structure(cls, *, tenant, user, attrs, components):
        with transaction.atomic():
            structure = RateStructure(**attrs)
            structure.full_clean()
            structure.save()
            cls._replace_components(structure=structure, components=components or [])
            cls._audit(tenant=tenant, user=user, action="rate_structure.created", structure=structure)
            return structure

    @classmethod
    def update_structure(cls, *, tenant, user, structure, attrs, components=None):
        with transaction.atomic():
            structure = RateStructure.objects.select_for_update().get(pk=structure.pk)
            for key, value in attrs.items():
                setattr(structure, key, value)
            structure.full_clean()
            structure.save()

            if components is not None:
                if structure.rate_cards.filter(status=RateCard.STATUS_ACTIVE).exists():
                    raise ValidationError(
                        {
                            "components": "You cannot change components on a rate structure that is used by an active rate card."
                        }
                    )
                cls._replace_components(structure=structure, components=components)

            cls._audit(tenant=tenant, user=user, action="rate_structure.updated", structure=structure)
            return structure

    @classmethod
    def delete_structure(cls, *, tenant, user, structure):
        cls._audit(tenant=tenant, user=user, action="rate_structure.deleted", structure=structure)
        structure.delete()

    @classmethod
    def clone_structure(cls, *, tenant, user, structure, name=None):
        with transaction.atomic():
            source = RateStructure.objects.prefetch_related("components").get(pk=structure.pk)
            cloned_structure = RateStructure(
                name=(name or f"{source.name} Copy").strip(),
                description=source.description,
                status=RateStructure.STATUS_DRAFT,
                currency_mode=source.currency_mode,
                rounding_scale=source.rounding_scale,
                is_default=False,
            )
            cloned_structure.full_clean()
            cloned_structure.save()

            for component in source.components.all().order_by("sequence", "id"):
                cloned_component = RateStructureComponent(
                    rate_structure=cloned_structure,
                    sequence=component.sequence,
                    code=component.code,
                    label=component.label,
                    value_type=component.value_type,
                    calculation_role=component.calculation_role,
                    is_required=component.is_required,
                    is_active=component.is_active,
                )
                cloned_component.full_clean()
                cloned_component.save()

            cls._audit(tenant=tenant, user=user, action="rate_structure.cloned", structure=cloned_structure)
            return cloned_structure

    @classmethod
    def _replace_components(cls, *, structure, components):
        structure.components.all().delete()
        for component_data in components:
            component = RateStructureComponent(rate_structure=structure, **component_data)
            component.full_clean()
            component.save()

    @classmethod
    def _snapshot_payload(cls, structure):
        return {
            "id": structure.id,
            "name": structure.name,
            "description": structure.description,
            "status": structure.status,
            "currency_mode": structure.currency_mode,
            "rounding_scale": structure.rounding_scale,
            "is_default": structure.is_default,
            "components": [
                {
                    "id": component.id,
                    "sequence": component.sequence,
                    "code": component.code,
                    "label": component.label,
                    "value_type": component.value_type,
                    "calculation_role": component.calculation_role,
                    "is_required": component.is_required,
                    "is_active": component.is_active,
                }
                for component in structure.components.all().order_by("sequence", "id")
            ],
        }

    @classmethod
    def _audit(cls, *, tenant, user, action, structure):
        payload_hash = hashlib.sha256(
            json.dumps(cls._snapshot_payload(structure), sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        ).hexdigest()
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=action,
            object_type="rate_structure",
            object_id=str(structure.id),
            payload_hash=payload_hash,
        )


class RateCardService:
    @classmethod
    def create_card(cls, *, tenant, user, attrs, lines):
        with transaction.atomic():
            card = RateCard(**attrs)
            card.full_clean()
            card.save()
            cls._replace_lines(card=card, lines=lines or [], strict_recalculate=card.status == RateCard.STATUS_ACTIVE)
            if card.status == RateCard.STATUS_ACTIVE:
                cls._validate_active_overlap(card)
            cls._audit(tenant=tenant, user=user, action="rate_card.created", card=card)
            return card

    @classmethod
    def update_card(cls, *, tenant, user, card, attrs, lines=None):
        with transaction.atomic():
            card = RateCard.objects.select_for_update().get(pk=card.pk)
            for key, value in attrs.items():
                setattr(card, key, value)
            card.full_clean()
            card.save()

            if lines is not None:
                cls._replace_lines(card=card, lines=lines, strict_recalculate=card.status == RateCard.STATUS_ACTIVE)
            elif card.status == RateCard.STATUS_ACTIVE:
                cls._recalculate_card_locked(card=card, strict=True)

            if card.status == RateCard.STATUS_ACTIVE:
                cls._validate_active_overlap(card)

            cls._audit(tenant=tenant, user=user, action="rate_card.updated", card=card)
            return card

    @classmethod
    def delete_card(cls, *, tenant, user, card):
        cls._audit(tenant=tenant, user=user, action="rate_card.deleted", card=card)
        card.delete()

    @classmethod
    def recalculate_card(cls, *, tenant, user, card, strict=True):
        with transaction.atomic():
            locked_card = RateCard.objects.select_for_update().select_related("rate_structure").get(pk=card.pk)
            cls._recalculate_card_locked(card=locked_card, strict=strict)
            cls._audit(tenant=tenant, user=user, action="rate_card.recalculated", card=locked_card)
            return locked_card

    @classmethod
    def activate_card(cls, *, tenant, user, card):
        with transaction.atomic():
            locked_card = RateCard.objects.select_for_update().get(pk=card.pk)
            locked_card.status = RateCard.STATUS_ACTIVE
            locked_card.full_clean()
            locked_card.save(update_fields=["status", "updated_at"])
            cls._recalculate_card_locked(card=locked_card, strict=True)
            cls._validate_active_overlap(locked_card)
            cls._audit(tenant=tenant, user=user, action="rate_card.activated", card=locked_card)
            return locked_card

    @classmethod
    def _replace_lines(cls, *, card, lines, strict_recalculate):
        card.lines.all().delete()
        for line_data in lines:
            component_values = line_data.pop("component_values", [])
            line = RateCardLine(rate_card=card, **line_data)
            line.full_clean()
            line.save()
            cls._replace_line_values(line=line, component_values=component_values)
            cls._recalculate_line(line=line, strict=strict_recalculate)

    @classmethod
    def _replace_line_values(cls, *, line, component_values):
        line.component_values.all().delete()
        for value_data in component_values:
            line_value = RateCardLineValue(rate_card_line=line, **value_data)
            line_value.full_clean()
            line_value.save()

    @classmethod
    def _recalculate_card_locked(cls, *, card, strict):
        card = (
            RateCard.objects.select_related("rate_structure")
            .prefetch_related("rate_structure__components", "lines__component_values__rate_structure_component")
            .get(pk=card.pk)
        )
        for line in card.lines.all().order_by("sequence", "id"):
            cls._recalculate_line(line=line, strict=strict)
        return card

    @classmethod
    def _recalculate_line(cls, *, line, strict):
        component_values = {
            value.rate_structure_component.code: value.numeric_value
            for value in line.component_values.all().select_related("rate_structure_component")
        }
        try:
            calculation = calculate_bill_rate_for_structure(
                rate_structure=line.rate_card.rate_structure,
                component_values=component_values,
                strict=strict,
            )
        except ValueError as exc:
            raise ValidationError({"lines": str(exc)}) from exc
        line.bill_rate = calculation["bill_rate"]
        line.full_clean()
        line.save(update_fields=["bill_rate", "updated_at"])

    @classmethod
    def _validate_active_overlap(cls, card):
        queryset = RateCard.objects.filter(
            role_definition=card.role_definition,
            status=RateCard.STATUS_ACTIVE,
        ).exclude(pk=card.pk)

        overlap_end_query = Q(end_date__isnull=True) | Q(end_date__gte=card.effective_date)
        queryset = queryset.filter(overlap_end_query)

        if card.end_date:
            queryset = queryset.filter(effective_date__lte=card.end_date)

        if queryset.exists():
            raise ValidationError(
                {
                    "status": "Another active rate card already overlaps this role and effective date range."
                }
            )

    @classmethod
    def _snapshot_payload(cls, card):
        return {
            "id": card.id,
            "name": card.name,
            "role_definition_id": card.role_definition_id,
            "currency": card.currency,
            "unit": card.unit,
            "effective_date": card.effective_date.isoformat(),
            "end_date": card.end_date.isoformat() if card.end_date else None,
            "rate_structure_id": card.rate_structure_id,
            "status": card.status,
            "notes": card.notes,
            "lines": [
                {
                    "id": line.id,
                    "sequence": line.sequence,
                    "supplier_id": line.supplier_id,
                    "location_label": line.location_label,
                    "bill_rate": str(line.bill_rate),
                    "component_values": [
                        {
                            "component_id": value.rate_structure_component_id,
                            "numeric_value": str(value.numeric_value),
                        }
                        for value in line.component_values.all().order_by("rate_structure_component__sequence", "id")
                    ],
                }
                for line in card.lines.all().order_by("sequence", "id")
            ],
        }

    @classmethod
    def _audit(cls, *, tenant, user, action, card):
        payload_hash = hashlib.sha256(
            json.dumps(cls._snapshot_payload(card), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=action,
            object_type="rate_card",
            object_id=str(card.id),
            payload_hash=payload_hash,
        )


class RateRuleService:
    @classmethod
    def create_rule(cls, *, tenant, user, attrs, conditions):
        with transaction.atomic():
            rule = RateRule(**attrs)
            rule.full_clean()
            rule.save()
            cls._replace_conditions(rule=rule, conditions=conditions or [])
            cls._audit(tenant=tenant, user=user, action="rate_rule.created", rule=rule)
            return rule

    @classmethod
    def update_rule(cls, *, tenant, user, rule, attrs, conditions=None):
        with transaction.atomic():
            rule = RateRule.objects.select_for_update().get(pk=rule.pk)
            for key, value in attrs.items():
                setattr(rule, key, value)
            rule.full_clean()
            rule.save()
            if conditions is not None:
                cls._replace_conditions(rule=rule, conditions=conditions)
            cls._audit(tenant=tenant, user=user, action="rate_rule.updated", rule=rule)
            return rule

    @classmethod
    def delete_rule(cls, *, tenant, user, rule):
        cls._audit(tenant=tenant, user=user, action="rate_rule.deleted", rule=rule)
        rule.delete()

    @classmethod
    def _replace_conditions(cls, *, rule, conditions):
        rule.conditions.all().delete()
        for condition_data in conditions:
            condition = RateRuleCondition(rate_rule=rule, **condition_data)
            condition.full_clean()
            condition.save()

    @classmethod
    def _snapshot_payload(cls, rule):
        return {
            "id": rule.id,
            "name": rule.name,
            "description": rule.description,
            "priority": rule.priority,
            "status": rule.status,
            "rate_structure_id": rule.rate_structure_id,
            "role_definition_id": rule.role_definition_id,
            "effective_date": rule.effective_date.isoformat(),
            "end_date": rule.end_date.isoformat() if rule.end_date else None,
            "action_type": rule.action_type,
            "action_value": str(rule.action_value),
            "stop_processing": rule.stop_processing,
            "conditions": [
                {
                    "id": condition.id,
                    "sequence": condition.sequence,
                    "joiner": condition.joiner,
                    "field_key": condition.field_key,
                    "operator": condition.operator,
                    "value_json": condition.value_json,
                }
                for condition in rule.conditions.all().order_by("sequence", "id")
            ],
        }

    @classmethod
    def _audit(cls, *, tenant, user, action, rule):
        payload_hash = hashlib.sha256(
            json.dumps(cls._snapshot_payload(rule), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        AuditEvent.objects.create(
            actor=user,
            tenant=tenant,
            action=action,
            object_type="rate_rule",
            object_id=str(rule.id),
            payload_hash=payload_hash,
        )
