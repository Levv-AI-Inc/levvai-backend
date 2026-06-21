from apps.approvals.engine import evaluate_chain
from apps.approvals.models import ApprovalChain


def build_work_order_approval_payload(work_order):
    intake = work_order.intake
    cost_center = work_order.cost_center or (intake.cost_center if intake else None)
    business_unit = cost_center.business_unit if cost_center else None
    site = work_order.site or (intake.site if intake else None)
    legal_entity = work_order.legal_entity or (site.legal_entity if site and site.legal_entity else None)
    supplier = work_order.supplier or (intake.supplier if intake else None)
    role_definition = work_order.role_definition or (intake.role_definition if intake else None)

    created_by_display = ""
    if work_order.created_by:
        created_by_display = (
            work_order.created_by.get_full_name().strip()
            or work_order.created_by.email
            or work_order.created_by.username
        )

    submitted_by_display = ""
    if work_order.submitted_by:
        submitted_by_display = (
            work_order.submitted_by.get_full_name().strip()
            or work_order.submitted_by.email
            or work_order.submitted_by.username
        )

    source_snapshot = work_order.source_snapshot or {}
    source_custom_fields = {}
    if intake and isinstance(intake.custom_fields, dict):
        source_custom_fields.update(intake.custom_fields)
    snapshot_custom_fields = source_snapshot.get("custom_fields")
    if isinstance(snapshot_custom_fields, dict):
        source_custom_fields.update(snapshot_custom_fields)
    intake_snapshot = source_snapshot.get("intake")
    if isinstance(intake_snapshot, dict):
        snapshot_intake_fields = intake_snapshot.get("custom_fields")
        if isinstance(snapshot_intake_fields, dict):
            source_custom_fields.update(snapshot_intake_fields)

    country = (
        getattr(site, "country", "")
        or (getattr(intake, "country", "") if intake else "")
        or source_snapshot.get("country", "")
        or (intake_snapshot.get("country", "") if isinstance(intake_snapshot, dict) else "")
    )
    region = (
        getattr(site, "state_province", "")
        or (getattr(intake, "state_province", "") if intake else "")
        or source_snapshot.get("state_province", "")
        or (intake_snapshot.get("state_province", "") if isinstance(intake_snapshot, dict) else "")
    )

    return {
        "job_title": role_definition.name if role_definition else (source_snapshot.get("job_title", "") or ""),
        "job_country": country,
        "job_region": region,
        "country": country,
        "region": region,
        "engagement_type": intake.engagement_type if intake else "staffing",
        "currency": work_order.currency,
        "budget_amount": work_order.budget_amount,
        "target_rate": work_order.bill_rate,
        "worker_count": 1,
        "start_date": work_order.start_date.isoformat() if work_order.start_date else None,
        "end_date": work_order.end_date.isoformat() if work_order.end_date else None,
        "cost_center_id": str(cost_center.pk) if cost_center else "",
        "cost_center": {
            "code": getattr(cost_center, "code", ""),
            "name": getattr(cost_center, "name", ""),
            "currency": getattr(cost_center, "currency", ""),
            "owner_email": getattr(cost_center, "owner_email", ""),
            "business_unit": {
                "code": getattr(business_unit, "code", ""),
                "name": getattr(business_unit, "name", ""),
            },
        },
        "site_id": str(site.pk) if site else "",
        "site": {
            "code": getattr(site, "code", ""),
            "name": getattr(site, "name", ""),
            "country": getattr(site, "country", ""),
            "state_province": getattr(site, "state_province", ""),
            "city": getattr(site, "city", ""),
            "timezone": getattr(site, "timezone", ""),
            "currency": getattr(site, "currency", ""),
        },
        "legal_entity": {
            "id": str(getattr(legal_entity, "id", "")),
            "name": getattr(legal_entity, "name", ""),
            "country": getattr(legal_entity, "country", ""),
            "currency": getattr(legal_entity, "currency", ""),
        },
        "supplier_id": str(supplier.pk) if supplier else "",
        "supplier": {
            "name": getattr(supplier, "name", ""),
            "supplier_type": getattr(supplier, "supplier_type", ""),
            "category": getattr(supplier, "category", ""),
            "status": getattr(supplier, "status", ""),
            "risk_level": getattr(supplier, "risk_level", ""),
            "compliance_status": getattr(supplier, "compliance_status", ""),
        },
        "created_by": created_by_display,
        "submitted_by": submitted_by_display,
        "is_urgent": bool(source_custom_fields.get("is_urgent", False)),
        "custom_fields": source_custom_fields,
    }


def resolve_work_order_approval_chain(work_order):
    payload = build_work_order_approval_payload(work_order)
    queryset = ApprovalChain.objects.filter(is_active=True).prefetch_related("conditions", "steps__approver").order_by(
        "priority", "name", "id"
    )
    for chain in queryset:
        evaluation = evaluate_chain(chain, payload)
        if evaluation["matched"]:
            return chain, evaluation
    return None, None
