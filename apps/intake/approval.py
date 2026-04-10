from apps.intake.models import IntakeRequest


def compute_approval_preview(intake):
    """Placeholder approval routing until policy engine is introduced."""
    if intake.engagement_type == IntakeRequest.ENGAGEMENT_SOW:
        return [
            {"step": 1, "approver_group": "Procurement", "reason": "SOW request"},
            {"step": 2, "approver_group": "Finance", "reason": "Budget review"},
        ]

    if intake.engagement_type == IntakeRequest.ENGAGEMENT_STAFFING:
        return [
            {"step": 1, "approver_group": "HiringManager", "reason": "Staffing request"},
            {"step": 2, "approver_group": "Finance", "reason": "Budget review"},
        ]

    return [
        {"step": 1, "approver_group": "Manager", "reason": "Default intake route"},
        {"step": 2, "approver_group": "Finance", "reason": "Budget review"},
    ]
