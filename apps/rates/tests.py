from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.rates.calculations import calculate_bill_rate_for_components, evaluate_rule
from apps.rates.models import RateRule, RateRuleCondition, RateStructureComponent


class FakeConditionManager:
    def __init__(self, conditions):
        self.conditions = conditions

    def all(self):
        return self

    def order_by(self, *args):
        return sorted(self.conditions, key=lambda item: (item.sequence, getattr(item, "id", 0)))


class RateCalculationsTests(SimpleTestCase):
    def test_calculate_bill_rate_with_percent_components(self):
        components = [
            SimpleNamespace(
                code="pay_rate",
                label="Pay Rate",
                value_type=RateStructureComponent.VALUE_CURRENCY,
                calculation_role=RateStructureComponent.ROLE_BASE,
                ROLE_BASE=RateStructureComponent.ROLE_BASE,
                ROLE_ADDITIVE_PERCENT=RateStructureComponent.ROLE_ADDITIVE_PERCENT,
                ROLE_ADDITIVE_AMOUNT=RateStructureComponent.ROLE_ADDITIVE_AMOUNT,
                is_required=True,
            ),
            SimpleNamespace(
                code="supplier_markup",
                label="Supplier Markup",
                value_type=RateStructureComponent.VALUE_PERCENTAGE,
                calculation_role=RateStructureComponent.ROLE_ADDITIVE_PERCENT,
                ROLE_BASE=RateStructureComponent.ROLE_BASE,
                ROLE_ADDITIVE_PERCENT=RateStructureComponent.ROLE_ADDITIVE_PERCENT,
                ROLE_ADDITIVE_AMOUNT=RateStructureComponent.ROLE_ADDITIVE_AMOUNT,
                is_required=True,
            ),
            SimpleNamespace(
                code="extra_markup",
                label="Extra Markup",
                value_type=RateStructureComponent.VALUE_PERCENTAGE,
                calculation_role=RateStructureComponent.ROLE_ADDITIVE_PERCENT,
                ROLE_BASE=RateStructureComponent.ROLE_BASE,
                ROLE_ADDITIVE_PERCENT=RateStructureComponent.ROLE_ADDITIVE_PERCENT,
                ROLE_ADDITIVE_AMOUNT=RateStructureComponent.ROLE_ADDITIVE_AMOUNT,
                is_required=False,
            ),
        ]

        calculation = calculate_bill_rate_for_components(
            components=components,
            component_values={
                "pay_rate": "70",
                "supplier_markup": "20",
                "extra_markup": "0",
            },
            rounding_scale=2,
            strict=True,
        )

        self.assertEqual(calculation["bill_rate"], Decimal("84.00"))

    def test_evaluate_rule_applies_multiplier_when_condition_matches(self):
        condition = SimpleNamespace(
            id=1,
            sequence=1,
            joiner=RateRuleCondition.JOIN_AND,
            field_key="hours",
            operator="gt",
            value_json="8",
        )
        rule = SimpleNamespace(
            action_type=RateRule.ACTION_MULTIPLY_BILL_RATE,
            action_value=Decimal("1.5000"),
            stop_processing=True,
            conditions=FakeConditionManager([condition]),
        )

        evaluation = evaluate_rule(
            rule,
            context={"hours": 10},
            base_bill_rate=Decimal("100.00"),
        )

        self.assertTrue(evaluation["matched"])
        self.assertEqual(evaluation["adjusted_bill_rate"], "150.0000")
