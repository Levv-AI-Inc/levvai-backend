from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.approvals.engine import evaluate_chain, normalize_condition_value


class ApprovalChainEngineTests(SimpleTestCase):
    def test_normalizes_between_values_as_numbers(self):
        field_definition = SimpleNamespace(data_type="number")
        value = normalize_condition_value(field_definition, "between", ["10", "20"])
        self.assertEqual([str(item) for item in value], ["10", "20"])

    def test_evaluates_match_all_chain(self):
        conditions = [
            SimpleNamespace(sequence=1, field_key="country", operator="equals", value_json="CA"),
            SimpleNamespace(sequence=2, field_key="budget_amount", operator="gte", value_json="1000"),
        ]
        steps = [
            SimpleNamespace(
                sequence=1,
                step_type="specific_user",
                approver_id=10,
                approver=SimpleNamespace(get_full_name=lambda: "Alice Approver", username="alice"),
                amount="1000.00",
                currency="USD",
            )
        ]
        chain = SimpleNamespace(
            MATCH_ALL="all",
            MATCH_ANY="any",
            match_strategy="all",
            conditions=SimpleNamespace(all=lambda: SimpleNamespace(order_by=lambda *args: conditions)),
            steps=SimpleNamespace(all=lambda: SimpleNamespace(order_by=lambda *args: steps)),
        )

        result = evaluate_chain(chain, {"country": "CA", "budget_amount": "1500"})

        self.assertTrue(result["matched"])
        self.assertEqual(1, len(result["resolved_steps"]))

    def test_evaluates_dynamic_custom_field(self):
        conditions = [
            SimpleNamespace(
                sequence=1,
                field_key="custom_fields.project_type",
                operator="contains",
                value_json="software",
            )
        ]
        chain = SimpleNamespace(
            MATCH_ALL="all",
            MATCH_ANY="any",
            match_strategy="all",
            conditions=SimpleNamespace(all=lambda: SimpleNamespace(order_by=lambda *args: conditions)),
            steps=SimpleNamespace(all=lambda: SimpleNamespace(order_by=lambda *args: [])),
        )

        result = evaluate_chain(chain, {"custom_fields": {"project_type": "Enterprise Software Rollout"}})

        self.assertTrue(result["matched"])

