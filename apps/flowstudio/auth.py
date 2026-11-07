import hmac

from django.conf import settings
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import BasePermission

from apps.accounts.session_scope import is_session_bound_to_tenant
from apps.common.permissions import IsTenantMember

STORE_TOKEN_HEADER = "HTTP_X_LEVV_STORE_TOKEN"


class StoreNotConfigured(APIException):
    status_code = 503
    default_detail = "The Flow Studio store is not configured."
    default_code = "store_not_configured"


def check_store_token(request):
    """Every store route needs the shared secret. Without it set on this service the store stays closed."""
    expected = settings.FLOW_STUDIO_STORE_TOKEN
    if not expected:
        raise StoreNotConfigured()
    supplied = request.META.get(STORE_TOKEN_HEADER, "")
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise PermissionDenied("Invalid store token.")


class StoreAccess(BasePermission):
    """
    With a session cookie the caller is that user: they must be an active member bound to this tenant. CSRF is enforced
    by SessionAuthentication once the user is authenticated. A cookie that does not resolve to a user is refused rather
    than treated as a service call. With no cookie and a valid token the call runs in the service context.
    """

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        if tenant is None or tenant.schema_name == "public":
            raise NotFound("Flow Studio data lives in a tenant.")
        if settings.SESSION_COOKIE_NAME not in request.COOKIES:
            return True
        if not IsTenantMember().has_permission(request, view):
            return False
        return is_session_bound_to_tenant(request, tenant)
