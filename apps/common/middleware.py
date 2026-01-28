from django.http import HttpResponseForbidden

from apps.accounts.models import Membership
from apps.common.tenant_context import set_tenant_id


class TenantContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = None
        if hasattr(request, "tenant"):
            tenant_id = getattr(request.tenant, "id", None)
        set_tenant_id(tenant_id)
        try:
            return self.get_response(request)
        finally:
            set_tenant_id(None)


class TenantMembershipMiddleware:
    """Enforces that authenticated users belong to the resolved tenant."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and hasattr(request, "tenant"):
            tenant = request.tenant
            if tenant and tenant.schema_name != "public":
                if not Membership.objects.filter(user=request.user, tenant_id=tenant.id, is_active=True).exists():
                    return HttpResponseForbidden("User is not a member of this tenant")
        return self.get_response(request)
