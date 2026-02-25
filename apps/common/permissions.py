from rest_framework.permissions import BasePermission

from apps.accounts.models import Membership


class IsTenantMember(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if not hasattr(request, "tenant"):
            return False
        if request.tenant.schema_name == "public":
            return True
        return Membership.objects.filter(user=request.user, tenant_id=request.tenant.id, status=Membership.STATUS_ACTIVE, is_active=True).exists()


class HasRole(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.tenant.schema_name == "public":
            return False
        required_roles = set(getattr(view, "required_roles", []) or [])
        if not required_roles:
            return True
        membership = Membership.objects.filter(
            user=request.user, tenant_id=request.tenant.id, status=Membership.STATUS_ACTIVE, is_active=True
        ).first()
        if not membership:
            return False
        return membership.role in required_roles
