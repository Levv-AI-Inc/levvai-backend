import re
from datetime import date
from decimal import Decimal, InvalidOperation

from apps.intake.models import IntakeRequest
from apps.masterdata.models import CustomField


def validate_intake_request(intake, strict=False):
    errors = []

    def add(field, code, message):
        errors.append({"field": field, "code": code, "message": message})

    required_fields = [
        ("engagement_type", intake.engagement_type),
        ("title", intake.title),
        ("description", intake.description),
        ("cost_center_id", intake.cost_center_id),
        ("site_id", intake.site_id),
        ("start_date", intake.start_date),
        ("end_date", intake.end_date),
        ("worker_count", intake.worker_count),
        ("budget_amount", intake.budget_amount),
        ("currency", intake.currency),
    ]

    for field, value in required_fields:
        if value in (None, "", []):
            add(field, "required", f"{field.replace('_', ' ').title()} is required.")

    if intake.start_date and intake.end_date and intake.start_date > intake.end_date:
        add("end_date", "date_order", "end_date must be on or after start_date.")

    if intake.worker_count is not None and intake.worker_count <= 0:
        add("worker_count", "min_value", "worker_count must be greater than 0.")
    if intake.target_rate is not None and intake.target_rate < 0:
        add("target_rate", "min_value", "target_rate must be greater than or equal to 0.")
    if intake.budget_amount is not None and intake.budget_amount < 0:
        add("budget_amount", "min_value", "budget_amount must be greater than or equal to 0.")

    if strict:
        _validate_custom_fields(intake.custom_fields or {}, add)

    return errors


def _validate_custom_fields(custom_fields, add_error):
    if not isinstance(custom_fields, dict):
        add_error("custom_fields", "type", "custom_fields must be an object.")
        return

    configs = {item.name: item.schema or {} for item in CustomField.objects.all()}

    for key in custom_fields.keys():
        if key not in configs:
            add_error(f"custom_fields.{key}", "unknown", "Unknown custom field.")

    for name, schema in configs.items():
        value = custom_fields.get(name, None)
        required = bool(schema.get("required", False))
        if required and value in (None, ""):
            add_error(f"custom_fields.{name}", "required", "This custom field is required.")
            continue
        if value in (None, ""):
            continue

        field_type = str(schema.get("type", "")).lower().strip()
        if field_type:
            _validate_custom_type(name, field_type, value, add_error)

        enum_values = schema.get("enum")
        if enum_values and isinstance(enum_values, list) and value not in enum_values:
            add_error(f"custom_fields.{name}", "enum", "Value must be one of configured options.")

        regex = schema.get("regex")
        if regex and isinstance(value, str):
            try:
                if re.fullmatch(regex, value) is None:
                    add_error(f"custom_fields.{name}", "regex", "Invalid format.")
            except re.error:
                add_error(f"custom_fields.{name}", "schema", "Invalid regex in custom field schema.")

        _validate_custom_bounds(name, schema, value, add_error)


def _validate_custom_type(name, field_type, value, add_error):
    field = f"custom_fields.{name}"
    if field_type == "string":
        if not isinstance(value, str):
            add_error(field, "type", "Expected string.")
    elif field_type == "number":
        if not isinstance(value, (int, float, Decimal)):
            add_error(field, "type", "Expected number.")
    elif field_type == "integer":
        if not isinstance(value, int):
            add_error(field, "type", "Expected integer.")
    elif field_type == "boolean":
        if not isinstance(value, bool):
            add_error(field, "type", "Expected boolean.")
    elif field_type == "date":
        if isinstance(value, date):
            return
        if not isinstance(value, str):
            add_error(field, "type", "Expected date string (YYYY-MM-DD).")
            return
        try:
            date.fromisoformat(value)
        except ValueError:
            add_error(field, "type", "Expected date string (YYYY-MM-DD).")


def _validate_custom_bounds(name, schema, value, add_error):
    field = f"custom_fields.{name}"
    min_value = schema.get("min")
    max_value = schema.get("max")
    min_length = schema.get("min_length")
    max_length = schema.get("max_length")

    if isinstance(value, str):
        if isinstance(min_length, int) and len(value) < min_length:
            add_error(field, "min_length", f"Minimum length is {min_length}.")
        if isinstance(max_length, int) and len(value) > max_length:
            add_error(field, "max_length", f"Maximum length is {max_length}.")

    if min_value is None and max_value is None:
        return

    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return

    if min_value is not None:
        try:
            if numeric < Decimal(str(min_value)):
                add_error(field, "min", f"Minimum value is {min_value}.")
        except (InvalidOperation, ValueError, TypeError):
            add_error(field, "schema", "Invalid min value in custom field schema.")
    if max_value is not None:
        try:
            if numeric > Decimal(str(max_value)):
                add_error(field, "max", f"Maximum value is {max_value}.")
        except (InvalidOperation, ValueError, TypeError):
            add_error(field, "schema", "Invalid max value in custom field schema.")
