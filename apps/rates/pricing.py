from decimal import Decimal, ROUND_HALF_UP

from apps.rates.calculations import calculate_bill_rate_for_structure, serialize_decimal
from apps.rates.models import RateCardLine


def resolve_intake_rate_card_pricing(*, intake, supplier=None, work_location_label=None, strict=True):
    if intake is None or not getattr(intake, "rate_card_id", None):
        return None

    supplier_obj = supplier or getattr(intake, "supplier", None)
    supplier_id = getattr(supplier_obj, "id", None)
    if not supplier_id:
        return None

    line = _select_rate_card_line(
        intake=intake,
        supplier_id=supplier_id,
        work_location_label=work_location_label,
    )
    if line is None:
        return None

    component_values = {}
    component_items = []
    for line_value in line.component_values.all().select_related("rate_structure_component"):
        component = line_value.rate_structure_component
        component_values[component.code] = line_value.numeric_value
        component_items.append(
            {
                "component_id": component.id,
                "code": component.code,
                "label": component.label,
                "value_type": component.value_type,
                "calculation_role": component.calculation_role,
                "numeric_value": line_value.numeric_value,
            }
        )

    try:
        calculation = calculate_bill_rate_for_structure(
            rate_structure=line.rate_card.rate_structure,
            component_values=component_values,
            strict=strict,
        )
    except ValueError:
        return None

    return {
        "rate_card_id": line.rate_card_id,
        "rate_card_name": line.rate_card.name,
        "rate_structure_id": line.rate_card.rate_structure_id,
        "rate_structure_name": line.rate_card.rate_structure.name,
        "rate_card_line_id": line.id,
        "supplier_id": line.supplier_id,
        "supplier_name": line.supplier.name,
        "location_label": line.location_label,
        "currency": line.rate_card.currency,
        "unit": line.rate_card.unit,
        "bill_rate": calculation["bill_rate"],
        "base_amount": calculation["base_amount"],
        "total_percent_markup": calculation["total_percent"],
        "total_fixed_markup": calculation["total_fixed_amount"],
        "components": component_items,
        "breakdown": calculation["breakdown"],
    }


def serialize_pricing_payload(pricing):
    if not pricing:
        return None

    return {
        "rate_card_id": pricing["rate_card_id"],
        "rate_card_name": pricing["rate_card_name"],
        "rate_structure_id": pricing["rate_structure_id"],
        "rate_structure_name": pricing["rate_structure_name"],
        "rate_card_line_id": pricing["rate_card_line_id"],
        "supplier_id": pricing["supplier_id"],
        "supplier_name": pricing["supplier_name"],
        "location_label": pricing["location_label"],
        "currency": pricing["currency"],
        "unit": pricing["unit"],
        "bill_rate": _serialize_decimal_2(pricing["bill_rate"]),
        "base_amount": _serialize_decimal_2(pricing["base_amount"]),
        "total_percent_markup": _serialize_decimal_2(pricing["total_percent_markup"]),
        "total_fixed_markup": _serialize_decimal_2(pricing["total_fixed_markup"]),
        "components": [
            {
                "component_id": component["component_id"],
                "code": component["code"],
                "label": component["label"],
                "value_type": component["value_type"],
                "calculation_role": component["calculation_role"],
                "numeric_value": _serialize_decimal_2(component["numeric_value"]),
            }
            for component in pricing["components"]
        ],
        "breakdown": [
            {
                **entry,
                "entered_value": _serialize_decimal_2(entry.get("entered_value", "0")),
            }
            for entry in pricing["breakdown"]
        ],
    }


def _select_rate_card_line(*, intake, supplier_id, work_location_label):
    lines = list(
        RateCardLine.objects.filter(
            rate_card_id=intake.rate_card_id,
            supplier_id=supplier_id,
        )
        .select_related("rate_card__rate_structure", "supplier")
        .prefetch_related("component_values__rate_structure_component")
        .order_by("sequence", "id")
    )
    if not lines:
        return None

    candidates = _location_candidates(intake=intake, work_location_label=work_location_label)

    for candidate in candidates:
        candidate_normalized = _normalize_location(candidate)
        if not candidate_normalized:
            continue
        for line in lines:
            line_normalized = _normalize_location(line.location_label)
            if line_normalized and line_normalized == candidate_normalized:
                return line

    for candidate in candidates:
        candidate_normalized = _normalize_location(candidate)
        if not candidate_normalized:
            continue
        for line in lines:
            line_normalized = _normalize_location(line.location_label)
            if line_normalized and (
                line_normalized in candidate_normalized or candidate_normalized in line_normalized
            ):
                return line

    for line in lines:
        if not _normalize_location(line.location_label):
            return line

    return lines[0]


def _location_candidates(*, intake, work_location_label):
    labels = []

    if work_location_label:
        labels.append(work_location_label)

    if getattr(intake, "site", None):
        labels.append(intake.site.name)
        labels.append(
            ", ".join(
                part
                for part in [
                    intake.site.city,
                    intake.site.state_province,
                    intake.site.country,
                ]
                if part
            )
        )

    labels.append(
        ", ".join(
            part
            for part in [
                getattr(intake, "city", ""),
                getattr(intake, "state_province", ""),
                getattr(intake, "country", ""),
            ]
            if part
        )
    )
    labels.append(getattr(intake, "city", ""))

    seen = set()
    deduped = []
    for label in labels:
        normalized = _normalize_location(label)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(label)
    return deduped


def _normalize_location(value):
    text = (value or "").strip().lower()
    text = text.replace(",", " ")
    return " ".join(text.split())


def _serialize_decimal_2(value):
    normalized = Decimal(str(value))
    rounded = normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return serialize_decimal(rounded)
