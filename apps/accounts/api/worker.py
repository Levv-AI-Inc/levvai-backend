from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import WorkerEngagement
from apps.accounts.profile import (
    build_worker_profile_metadata,
    build_worker_session_metadata,
    get_active_worker_profile,
    set_current_worker_engagement,
)
from apps.common.permissions import IsWorkerProfile


class WorkerContextView(APIView):
    permission_classes = [IsAuthenticated, IsWorkerProfile]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        worker_profile = get_active_worker_profile(request.user)
        return Response(
            {
                "profile": build_worker_profile_metadata(),
                "worker": build_worker_session_metadata(request, worker_profile),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        worker_profile = get_active_worker_profile(request.user)
        engagement_id = request.data.get("engagement_id") if hasattr(request.data, "get") else None
        if not engagement_id:
            return Response({"detail": "engagement_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        engagements = list(
            worker_profile.engagements.filter(status=WorkerEngagement.STATUS_ACTIVE).select_related("tenant")
        )
        selected = None
        for engagement in engagements:
            if str(engagement.id) == str(engagement_id):
                selected = engagement
                break
        if not selected:
            return Response({"detail": "Worker engagement was not found."}, status=status.HTTP_404_NOT_FOUND)

        set_current_worker_engagement(request, selected)
        return Response(
            {
                "profile": build_worker_profile_metadata(),
                "worker": build_worker_session_metadata(request, worker_profile),
            },
            status=status.HTTP_200_OK,
        )


class WorkerTimesheetContextView(WorkerContextView):
    pass
