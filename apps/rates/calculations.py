from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from apps.rates.models import RateRule, RateRuleCondition


@dataclass(frozen=True)
class RuleFieldDefinition:
    key: str
    label: str
    data_type: str
    supported_operators: tuple[str, ...]


RULE_OPERATORS = {
    "equals": {"key": "equals", "label": "Equals", "value_required": True},
    "not_equals": {"key": "not_equals", "label": "Does Not Equal", "value_required": True},
    "gt": {"key": "gt", "label": "Greater Than", "value_required": True},
    "gte": {"key": "gte", "label": "Greater Than or Equal", "value_required": True},
    "lt": {"key": "lt", "label": "Less Than", "value_required": True},
    "lte": {"key": "lte", "label": "Less Than or Equal", "value_required": True},
    "in": {"key": "in", "label": "In", "value_required": True},
    "not_in": {"key": "not_in", "label": "Not In", "value_required": True},
}

RULE_FIELD_DEFINITIONS = [
    RuleFieldDefinition(
        key="hours",
        label="Hours",
        data_type="number",
        supported_operators=("equals", "not_equals", "gt", "gte", "lt", "lte", "in", "not_in"),
    ),
    RuleFieldDefinition(
        key="day_of_week",
        label="Day of Week",
        data_type="text",
        supported_operators=("equals", "not_equals", "in", "not_in"),
    ),
    RuleFieldDefinition(
        key="is_holiday",
        label="Is Holiday",
        data_type="boolean",
        supported_operators=("equals", "not_equals"),
    ),
    RuleFieldDefinition(
        key="shift_code",
        label="Shift Code",
        data_type="text",
        supported_operators=("equals", "not_equals", "in", "not_in"),
    ),
    RuleFieldDefinition(
        key="location_label",
        label="Location",
        data_type="text",
        supported_operators=("equals", "not_equals", "in", "not_in"),
    ),
    RuleFieldDefinition(
        key="supplier_id",
        label="Supplier",
        data_type="number",
        supported_operators=("equals", "not_equals", "in", "not_in"),
    ),
    RuleFieldDefinition(
        key="role_definition_id",
        label="Role",
        data_type="number",
        supported_operators=("equals", "not_equals", "in", "not_in"),
    ),
]

RULE_FIELD_MAP = {definition.key: definition for definition in RULE_FIELD_DEFINITIONS}


def get_rule_field_definition(field_key):
    return RULE_FIELD_MAP.get(field_key)


def build_rule_catalog_response():
    return {
        "operators": list(RULE_OPERATORS.values()),
        "fields": [
            {
                "key": definition.key,
                "label": definition.label,
                "data_type": definition.data_type,
                "supported_operators": [RULE_OPERATORS[key] for key in definition.supported_operators],
            }
            for definition in RULE_FIELD_DEFINITIONS
        ],
    }


def normalize_decimal(value, *, field_label="value"):
    if value in (None, ""):
        raise ValueError(f"{field_label} is required.")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_label} must be a valid number.") from exc


def quantize_decimal(value, scale):
    quantizer = Decimal("1").scaleb(-scale)
    return value.quantize(quantizer, rounding=ROUND_HALF_UP)


def serialize_decimal(value):
    return format(value, "f")


def calculate_bill_rate_for_components(*, components, component_values, rounding_scale=2, strict=True):
    base_amount = None
    total_percent = Decimal("0")
    total_fixed_amount = Decimal("0")
    normalized_values = {}
    breakdown = []

    for component in components:
        raw_value = component_values.get(component.code)
        is_missing = raw_value in (None, "")
        if is_missing and component.is_required and strict:
            raise ValueError(f"Missing value for required component '{component.label}'.")

        numeric_value = Decimal("0")
        if not is_missing:
            numeric_value = normalize_decimal(raw_value, field_label=component.label)
            if numeric_value < 0:
                raise ValueError(f"{component.label} must be greater than or equal to 0.")

        normalized_values[component.code] = numeric_value
        breakdown.append(
            {
                "component_code": component.code,
                "component_label": component.label,
                "value_type": component.value_type,
                "calculation_role": component.calculation_role,
                "entered_value": serialize_decimal(numeric_value),
                "is_missing": is_missing,
            }
        )

        if component.calculation_role == component.ROLE_BASE:
            base_amount = numeric_value
        elif component.calculation_role == component.ROLE_ADDITIVE_PERCENT:
            total_percent += numeric_value
        elif component.calculation_role == component.ROLE_ADDITIVE_AMOUNT:
            total_fixed_amount += numeric_value

    if base_amount is None:
        if strict:
            raise ValueError("Rate structure must include a base component.")
        base_amount = Decimal("0")

    bill_rate = base_amount * (Decimal("1") + (total_percent / Decimal("100"))) + total_fixed_amount
    bill_rate = quantize_decimal(bill_rate, rounding_scale)

    return {
        "base_amount": base_amount,
        "total_percent": total_percent,
        "total_fixed_amount": total_fixed_amount,
        "bill_rate": bill_rate,
        "normalized_values": normalized_values,
        "breakdown": breakdown,
    }


def calculate_bill_rate_for_structure(*, rate_structure, component_values, strict=True):
    components = list(rate_structure.components.filter(is_active=True).order_by("sequence", "id"))
    return calculate_bill_rate_for_components(
        components=components,
        component_values=component_values,
        rounding_scale=rate_structure.rounding_scale,
        strict=strict,
    )


def normalize_rule_condition_value(field_definition, operator, value):
    if operator not in field_definition.supported_operators:
        raise ValueError("Operator is not supported for this field.")

    if operator in {"in", "not_in"}:
        if not isinstance(value, list) or not value:
            raise ValueError("Value must be a non-empty list.")
        values = value
    else:
        values = value

    if field_definition.data_type == "number":
        if isinstance(values, list):
            return [serialize_decimal(normalize_decimal(item, field_label=field_definition.label)) for item in values]
        return serialize_decimal(normalize_decimal(values, field_label=field_definition.label))

    if field_definition.data_type == "boolean":
        if isinstance(values, list):
            raise ValueError("Boolean conditions do not support list values.")
        if isinstance(values, bool):
            return values
        normalized = str(values).strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
        raise ValueError("Value must be true or false.")

    if isinstance(values, list):
        normalized_values = []
        for item in values:
            text = str(item).strip()
            if not text:
                raise ValueError("Value entries may not be blank.")
            normalized_values.append(text)
        return normalized_values

    text = str(values).strip()
    if not text:
        raise ValueError("Value may not be blank.")
    return text


def normalize_boolean(value, *, field_label="value"):
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{field_label} must be true or false.")


def _evaluate_condition(condition, context):
    actual_value = context.get(condition.field_key)
    expected_value = condition.value_json
    field_definition = get_rule_field_definition(condition.field_key)
    operator = condition.operator

    matched = False
    if field_definition and field_definition.data_type == "number":
        actual_decimal = normalize_decimal(actual_value, field_label=field_definition.label) if actual_value not in (None, "") else None
        if operator == "equals":
            matched = actual_decimal == Decimal(str(expected_value))
        elif operator == "not_equals":
            matched = actual_decimal != Decimal(str(expected_value))
        elif operator == "gt":
            matched = actual_decimal is not None and actual_decimal > Decimal(str(expected_value))
        elif operator == "gte":
            matched = actual_decimal is not None and actual_decimal >= Decimal(str(expected_value))
        elif operator == "lt":
            matched = actual_decimal is not None and actual_decimal < Decimal(str(expected_value))
        elif operator == "lte":
            matched = actual_decimal is not None and actual_decimal <= Decimal(str(expected_value))
        elif operator == "in":
            matched = actual_decimal is not None and actual_decimal in {Decimal(str(item)) for item in expected_value}
        elif operator == "not_in":
            matched = actual_decimal is not None and actual_decimal not in {Decimal(str(item)) for item in expected_value}
    elif field_definition and field_definition.data_type == "boolean":
        actual_bool = normalize_boolean(actual_value, field_label=field_definition.label) if actual_value is not None else False
        if operator == "equals":
            matched = actual_bool is expected_value
        elif operator == "not_equals":
            matched = actual_bool is not expected_value
    else:
        actual_text = "" if actual_value is None else str(actual_value).strip()
        if operator == "equals":
            matched = actual_text == expected_value
        elif operator == "not_equals":
            matched = actual_text != expected_value
        elif operator == "in":
            matched = actual_text in expected_value
        elif operator == "not_in":
            matched = actual_text not in expected_value

    return {
        "sequence": condition.sequence,
        "joiner": condition.joiner,
        "field_key": condition.field_key,
        "field_label": field_definition.label if field_definition else condition.field_key,
        "operator": operator,
        "expected_value": expected_value,
        "actual_value": actual_value,
        "matched": matched,
    }


def evaluate_rule(rate_rule, *, context, base_bill_rate):
    ordered_conditions = list(rate_rule.conditions.all().order_by("sequence", "id"))
    if not ordered_conditions:
        raise ValueError("Rate rule must include at least one condition.")

    condition_results = []
    overall_match = None

    for condition in ordered_conditions:
        result = _evaluate_condition(condition, context)
        condition_results.append(result)
        if overall_match is None:
            overall_match = result["matched"]
        elif condition.joiner == RateRuleCondition.JOIN_OR:
            overall_match = overall_match or result["matched"]
        else:
            overall_match = overall_match and result["matched"]

    current_bill_rate = normalize_decimal(base_bill_rate, field_label="base_bill_rate")
    adjusted_bill_rate = current_bill_rate

    if overall_match:
        action_value = Decimal(str(rate_rule.action_value))
        if rate_rule.action_type == RateRule.ACTION_MULTIPLY_BILL_RATE:
            adjusted_bill_rate = current_bill_rate * action_value
        elif rate_rule.action_type == RateRule.ACTION_ADD_PERCENT:
            adjusted_bill_rate = current_bill_rate * (Decimal("1") + (action_value / Decimal("100")))
        elif rate_rule.action_type == RateRule.ACTION_ADD_AMOUNT:
            adjusted_bill_rate = current_bill_rate + action_value
        adjusted_bill_rate = quantize_decimal(adjusted_bill_rate, 4)

    return {
        "matched": bool(overall_match),
        "base_bill_rate": serialize_decimal(current_bill_rate),
        "adjusted_bill_rate": serialize_decimal(adjusted_bill_rate),
        "stop_processing": rate_rule.stop_processing,
        "condition_results": condition_results,
    }
