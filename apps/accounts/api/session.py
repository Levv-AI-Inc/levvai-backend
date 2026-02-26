from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership
from apps.accounts.session_scope import is_session_bound_to_tenant


class SessionStatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"authenticated": False, "detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not request.user.is_authenticated:
            return Response({"authenticated": False}, status=status.HTTP_401_UNAUTHORIZED)

        if not is_session_bound_to_tenant(request, tenant):
            return Response(
                {"authenticated": False, "detail": "Session is not valid for this tenant."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        membership = (
            Membership.objects.filter(
                user=request.user,
                tenant=tenant,
                status=Membership.STATUS_ACTIVE,
                is_active=True,
            )
            .select_related("tenant")
            .first()
        )
        if not membership:
            return Response({"authenticated": False, "detail": "User is not a member of this tenant."}, status=status.HTTP_403_FORBIDDEN)

        return Response(
            {
                "authenticated": True,
                "user": {
                    "id": request.user.id,
                    "email": request.user.email,
                    "first_name": request.user.first_name,
                    "last_name": request.user.last_name,
                },
                "membership": {
                    "role": membership.role,
                    "tenant_id": membership.tenant_id,
                },
            },
            status=status.HTTP_200_OK,
        )
