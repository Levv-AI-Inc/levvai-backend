from django.contrib.auth import logout
from django.http import HttpResponseForbidden

from apps.accounts.models import Membership
from apps.accounts.profile import user_has_active_worker_engagement
from apps.accounts.session_scope import is_session_bound_to_tenant
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
    """Enforces tenant membership unless the session is a global worker profile."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and hasattr(request, "tenant"):
            tenant = request.tenant
            if tenant and tenant.schema_name != "public":
                is_bound_member = is_session_bound_to_tenant(request, tenant) and Membership.objects.filter(
                    user=request.user,
                    tenant_id=tenant.id,
                    status=Membership.STATUS_ACTIVE,
                    is_active=True,
                ).exists()
                if is_bound_member:
                    return self.get_response(request)

                if user_has_active_worker_engagement(request.user):
                    return self.get_response(request)

                if not is_session_bound_to_tenant(request, tenant):
                    logout(request)
                    return HttpResponseForbidden("Session is not valid for this tenant")
                return HttpResponseForbidden("User is not a member of this tenant")
        return self.get_response(request)
