from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_date, parse_datetime

from apps.approvals.catalog import OPERATOR_DEFINITIONS, get_field_definition


NO_VALUE_OPERATORS = {
    key for key, value in OPERATOR_DEFINITIONS.items() if not value["value_required"]
}


def normalize_condition_value(field_definition, operator, value):
    if operator in NO_VALUE_OPERATORS:
        return None

    if operator == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError("Between operator requires a two-item list.")
        return [coerce_scalar(field_definition.data_type, item) for item in value]

    if operator in {"in", "not_in"}:
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{operator} operator requires a list value.")
        return [coerce_scalar(field_definition.data_type, item) for item in value]

    return coerce_scalar(field_definition.data_type, value)


def evaluate_chain(chain, payload):
    condition_results = [
        evaluate_condition(condition, payload)
        for condition in chain.conditions.all().order_by("sequence", "id")
    ]

    if not condition_results:
        matched = True
    elif chain.match_strategy == chain.MATCH_ANY:
        matched = any(result["matched"] for result in condition_results)
    else:
        matched = all(result["matched"] for result in condition_results)

    return {
        "matched": matched,
        "match_strategy": chain.match_strategy,
        "condition_results": condition_results,
        "resolved_steps": [
            {
                "sequence": step.sequence,
                "step_type": step.step_type,
                "approver_id": step.approver_id,
                "approver_name": step.approver.get_full_name().strip() or step.approver.username,
                "amount": str(step.amount),
                "currency": step.currency,
            }
            for step in chain.steps.all().order_by("sequence", "id")
        ],
    }


def evaluate_condition(condition, payload):
    field_definition = get_field_definition(condition.field_key)
    actual_value = resolve_field_value(payload, condition.field_key, field_definition.resolver_path if field_definition else None)
    expected_value = condition.value_json
    matched = evaluate_operator(
        operator=condition.operator,
        actual_value=actual_value,
        expected_value=expected_value,
        data_type=field_definition.data_type if field_definition else "dynamic",
    )
    return {
        "sequence": condition.sequence,
        "field_key": condition.field_key,
        "field_label": field_definition.label if field_definition else condition.field_key,
        "operator": condition.operator,
        "expected_value": expected_value,
        "actual_value": actual_value,
        "matched": matched,
    }


def resolve_field_value(payload, field_key, resolver_path=None):
    if not isinstance(payload, dict):
        return None

    if field_key in payload:
        return payload[field_key]

    path = resolver_path or field_key
    current = payload
    for chunk in path.split("."):
        if isinstance(current, dict) and chunk in current:
            current = current[chunk]
            continue
        return None
    return current


def evaluate_operator(*, operator, actual_value, expected_value, data_type):
    if operator == "is_blank":
        return is_blank(actual_value)
    if operator == "is_not_blank":
        return not is_blank(actual_value)
    if operator == "is_true":
        return coerce_bool(actual_value) is True
    if operator == "is_false":
        return coerce_bool(actual_value) is False

    if is_blank(actual_value):
        return False

    if operator == "contains":
        return contains_value(actual_value, expected_value)
    if operator == "not_contains":
        return not contains_value(actual_value, expected_value)
    if operator == "starts_with":
        return str(actual_value).strip().lower().startswith(str(expected_value).strip().lower())
    if operator == "ends_with":
        return str(actual_value).strip().lower().endswith(str(expected_value).strip().lower())
    if operator == "in":
        return in_values(actual_value, expected_value, data_type)
    if operator == "not_in":
        return not in_values(actual_value, expected_value, data_type)
    if operator == "between":
        lower, upper = expected_value
        actual = coerce_scalar(data_type, actual_value)
        return lower <= actual <= upper

    left = coerce_scalar(data_type, actual_value)
    right = coerce_scalar(data_type, expected_value)

    if operator == "equals":
        return left == right
    if operator == "not_equals":
        return left != right
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right

    raise ValueError(f"Unsupported operator: {operator}")


def contains_value(actual_value, expected_value):
    if isinstance(actual_value, str):
        return str(expected_value).strip().lower() in actual_value.strip().lower()
    if isinstance(actual_value, (list, tuple, set)):
        expected = normalize_text(expected_value)
        return any(normalize_text(item) == expected for item in actual_value)
    if isinstance(actual_value, dict):
        expected = normalize_text(expected_value)
        return expected in {normalize_text(key) for key in actual_value.keys()}
    return normalize_text(expected_value) in normalize_text(actual_value)


def in_values(actual_value, expected_value, data_type):
    actual = actual_value
    candidates = [coerce_scalar(data_type, item) for item in expected_value]
    if isinstance(actual, (list, tuple, set)):
        actual_items = {coerce_scalar(data_type, item) for item in actual}
        return bool(actual_items & set(candidates))
    return coerce_scalar(data_type, actual) in candidates


def coerce_scalar(data_type, value):
    if data_type in {"number", "currency"}:
        return coerce_decimal(value)
    if data_type == "boolean":
        coerced = coerce_bool(value)
        if coerced is None:
            raise ValueError("Boolean value could not be parsed.")
        return coerced
    if data_type == "date":
        return coerce_date(value)
    if data_type == "datetime":
        return coerce_datetime(value)
    return normalize_text(value)


def coerce_decimal(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ValueError("Numeric value could not be parsed.")


def coerce_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    return None


def coerce_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    parsed = parse_date(str(value))
    if not parsed:
        parsed_dt = parse_datetime(str(value))
        if parsed_dt:
            return parsed_dt.date()
        raise ValueError("Date value could not be parsed.")
    return parsed


def coerce_datetime(value):
    if isinstance(value, datetime):
        return value
    parsed = parse_datetime(str(value))
    if not parsed:
        raise ValueError("Datetime value could not be parsed.")
    return parsed


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def is_blank(value):
    return value in {None, ""} or value == [] or value == {} or value == ()

