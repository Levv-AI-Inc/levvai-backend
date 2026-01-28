from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class TenantJWTAuthentication(JWTAuthentication):
    """Ensures tenant_id in JWT matches the resolved tenant."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        tenant_id = token.get("tenant_id")
        if hasattr(request, "tenant") and request.tenant.schema_name != "public":
            if not tenant_id or str(tenant_id) != str(request.tenant.id):
                raise AuthenticationFailed("JWT tenant_id does not match request tenant")
        return user, token
