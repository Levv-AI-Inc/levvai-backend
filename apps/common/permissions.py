from rest_framework.permissions import BasePermission

from apps.accounts.models import Membership
from apps.accounts.profile import user_has_active_worker_engagement


def get_active_tenant_membership(request):
    if not hasattr(request, "tenant"):
        return None
    if request.tenant.schema_name == "public":
        return None
    return Membership.objects.filter(
        user=request.user,
        tenant_id=request.tenant.id,
        status=Membership.STATUS_ACTIVE,
        is_active=True,
    ).first()


class IsTenantMember(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not hasattr(request, "tenant"):
            return False
        if request.tenant.schema_name == "public":
            return True
        return get_active_tenant_membership(request) is not None


class HasRole(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.tenant.schema_name == "public":
            return False
        required_roles = set(getattr(view, "required_roles", []) or [])
        if not required_roles:
            return True
        membership = get_active_tenant_membership(request)
        if not membership:
            return False
        return membership.role in required_roles


class IsWorkerProfile(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return user_has_active_worker_engagement(request.user)


class IsNonWorkerProfile(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return not user_has_active_worker_engagement(request.user)
