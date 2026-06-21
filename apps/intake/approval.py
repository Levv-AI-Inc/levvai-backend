from apps.approvals.engine import evaluate_chain
from apps.approvals.models import ApprovalChain


def build_intake_approval_payload(intake):
    cost_center = intake.cost_center
    business_unit = cost_center.business_unit if cost_center else None
    site = intake.site
    legal_entity = intake.legal_entity or (site.legal_entity if site and site.legal_entity else None)
    supplier = intake.supplier
    role_definition = intake.role_definition

    created_by_display = ""
    if intake.created_by:
        created_by_display = (
            intake.created_by.get_full_name().strip()
            or intake.created_by.email
            or intake.created_by.username
        )

    submitted_by_display = ""
    if intake.submitted_by:
        submitted_by_display = (
            intake.submitted_by.get_full_name().strip()
            or intake.submitted_by.email
            or intake.submitted_by.username
        )

    country = intake.country or (site.country if site else "")
    region = intake.state_province or (site.state_province if site else "")
    city = intake.city or (site.city if site else "")
    job_title = role_definition.name if role_definition else (intake.title or "")

    return {
        "job_title": job_title,
        "job_country": country,
        "job_region": region,
        "country": country,
        "region": region,
        "engagement_type": intake.engagement_type,
        "currency": intake.currency,
        "budget_amount": intake.budget_amount,
        "target_rate": intake.target_rate,
        "worker_count": intake.worker_count,
        "start_date": intake.start_date.isoformat() if intake.start_date else None,
        "end_date": intake.end_date.isoformat() if intake.end_date else None,
        "cost_center_id": str(intake.cost_center_id) if intake.cost_center_id else "",
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
        "site_id": str(intake.site_id) if intake.site_id else "",
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
            "id": getattr(legal_entity, "id", ""),
            "name": getattr(legal_entity, "name", ""),
            "country": getattr(legal_entity, "country", ""),
            "currency": getattr(legal_entity, "currency", ""),
        },
        "supplier_id": str(intake.supplier_id) if intake.supplier_id else "",
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
        "is_urgent": bool((intake.custom_fields or {}).get("is_urgent", False)),
        "custom_fields": intake.custom_fields or {},
        "city": city,
    }


def resolve_intake_approval_chain(intake):
    payload = build_intake_approval_payload(intake)
    queryset = ApprovalChain.objects.filter(is_active=True).prefetch_related("conditions", "steps__approver").order_by(
        "priority", "name", "id"
    )
    for chain in queryset:
        evaluation = evaluate_chain(chain, payload)
        if evaluation["matched"]:
            return chain, evaluation
    return None, None


def compute_approval_preview(intake):
    snapshot = intake.approval_chain_snapshot or {}
    resolved_steps = snapshot.get("resolved_steps")
    if isinstance(resolved_steps, list) and resolved_steps:
        return [
            {
                "step": item.get("sequence"),
                "approver_group": item.get("approver_name") or f"User #{item.get('approver_id')}",
                "reason": f"Threshold {item.get('amount')} {item.get('currency')}",
            }
            for item in resolved_steps
        ]

    chain, evaluation = resolve_intake_approval_chain(intake)
    if not chain or not evaluation:
        return []

    return [
        {
            "step": item.get("sequence"),
            "approver_group": item.get("approver_name") or f"User #{item.get('approver_id')}",
            "reason": f"Threshold {item.get('amount')} {item.get('currency')}",
        }
        for item in evaluation.get("resolved_steps", [])
    ]
