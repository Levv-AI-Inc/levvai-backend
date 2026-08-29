from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.profile import (
    build_membership_metadata,
    build_worker_profile_metadata,
    build_worker_session_metadata,
    get_active_worker_profile,
)
from apps.accounts.session_scope import is_session_bound_to_tenant
from apps.common.permissions import get_active_tenant_membership


class SessionStatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"authenticated": False, "detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not request.user.is_authenticated:
            return Response({"authenticated": False}, status=status.HTTP_401_UNAUTHORIZED)

        membership = get_active_tenant_membership(request)
        worker_profile = get_active_worker_profile(request.user)

        if membership and is_session_bound_to_tenant(request, tenant):
            membership_metadata = build_membership_metadata(membership)
            profile = {
                "type": membership_metadata["profile_type"],
                **membership_metadata,
            }
            return Response(
                {
                    "authenticated": True,
                    "user": {
                        "id": request.user.id,
                        "email": request.user.email,
                        "first_name": request.user.first_name,
                        "last_name": request.user.last_name,
                    },
                    "profile": profile,
                    "membership": membership_metadata,
                },
                status=status.HTTP_200_OK,
            )

        if worker_profile:
            worker = build_worker_session_metadata(request, worker_profile)
            if worker["engagements"]:
                return Response(
                    {
                        "authenticated": True,
                        "user": {
                            "id": request.user.id,
                            "email": request.user.email,
                            "first_name": request.user.first_name,
                            "last_name": request.user.last_name,
                        },
                        "profile": build_worker_profile_metadata(),
                        "worker": worker,
                    },
                    status=status.HTTP_200_OK,
                )

        if membership:
            return Response(
                {"authenticated": False, "detail": "Session is not valid for this tenant."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        else:
            return Response({"authenticated": False, "detail": "User is not a member of this tenant."}, status=status.HTTP_403_FORBIDDEN)
