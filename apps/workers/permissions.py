from django.db.models import Q

from apps.accounts.models import Membership
from apps.policies.models import WorkflowRequirement


INTERNAL_VIEW_ROLES = {
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_BUSINESS,
    Membership.ROLE_FINANCE,
    Membership.ROLE_VIEWER,
}

INTERNAL_EDIT_ROLES = {
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_BUSINESS,
}


def get_membership(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or not request.user or not request.user.is_authenticated:
        return None
    return Membership.objects.filter(
        user=request.user,
        tenant=tenant,
        status=Membership.STATUS_ACTIVE,
        is_active=True,
    ).first()


def can_access_worker(request, worker):
    if worker.user_id == getattr(request.user, "id", None):
        return True
    membership = get_membership(request)
    if not membership:
        return False
    if membership.role in INTERNAL_VIEW_ROLES:
        return True
    if membership.role == Membership.ROLE_SUPPLIER and membership.supplier_id:
        return worker.engagements.filter(
            Q(work_order__supplier_id=membership.supplier_id)
            | Q(engagement__work_order__supplier_id=membership.supplier_id)
        ).exists()
    return False


def can_update_activity(request, activity):
    worker_engagement = activity.block.run.worker_engagement
    worker = worker_engagement.worker
    if worker.user_id == getattr(request.user, "id", None):
        return activity.owner == WorkflowRequirement.OWNER_WORKER

    membership = get_membership(request)
    if not membership:
        return False
    if membership.role in INTERNAL_EDIT_ROLES:
        return True
    if membership.role == Membership.ROLE_SUPPLIER:
        work_order = worker_engagement.resolved_work_order
        return (
            activity.owner == WorkflowRequirement.OWNER_SUPPLIER
            and membership.supplier_id
            == getattr(work_order, "supplier_id", None)
        )
    return False


def can_manage_worker(request):
    membership = get_membership(request)
    return bool(membership and membership.role in INTERNAL_EDIT_ROLES)
