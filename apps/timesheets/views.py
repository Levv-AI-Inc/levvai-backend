from django.core.paginator import EmptyPage, Paginator
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership
from apps.accounts.profile import (
    NON_WORKER_TENANT_ROLES,
    build_worker_profile_metadata,
    get_active_worker_profile,
)
from apps.common.permissions import HasRole, IsTenantMember, IsWorkerProfile, get_active_tenant_membership
from apps.timesheets.models import Timesheet
from apps.timesheets.serializers import (
    CostAllocationRequestSerializer,
    TimesheetDecisionSerializer,
    TimesheetDetailSerializer,
    TimesheetListSerializer,
    TimesheetWriteSerializer,
)
from apps.timesheets.services import (
    TimesheetPermissionError,
    TimesheetService,
    TimesheetTransitionError,
    TimesheetValidationError,
)


READ_ROLES = list(NON_WORKER_TENANT_ROLES)
APPROVER_ROLES = [
    Membership.ROLE_ADMIN,
    Membership.ROLE_MANAGER,
    Membership.ROLE_FINANCE,
]


class WorkerTimesheetContextView(APIView):
    permission_classes = [IsAuthenticated, IsWorkerProfile]

    def get(self, request):
        worker_profile = _require_worker_profile(request)
        context = TimesheetService.worker_context(request, worker_profile)
        context["profile"] = build_worker_profile_metadata()
        context["timesheets"] = TimesheetListSerializer(context["timesheets"], many=True).data
        return Response(context, status=status.HTTP_200_OK)


class WorkerTimesheetListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsWorkerProfile]
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100

    def get(self, request):
        worker_profile = _require_worker_profile(request)
        queryset = TimesheetService.worker_queryset(worker_profile)
        queryset = _apply_timesheet_filters(queryset, request)
        page_data = _paginate_queryset(request, queryset, self.DEFAULT_PAGE_SIZE, self.MAX_PAGE_SIZE)
        return Response(
            {
                "results": TimesheetListSerializer(page_data["records"], many=True).data,
                "pagination": page_data["pagination"],
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        worker_profile = _require_worker_profile(request)
        serializer = TimesheetWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            timesheet = TimesheetService.create_for_worker(
                worker_profile=worker_profile,
                user=request.user,
                attrs=dict(serializer.validated_data),
            )
        except TimesheetValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response(TimesheetDetailSerializer(timesheet).data, status=status.HTTP_201_CREATED)


class WorkerTimesheetDetailView(APIView):
    permission_classes = [IsAuthenticated, IsWorkerProfile]

    def get(self, request, timesheet_id):
        worker_profile = _require_worker_profile(request)
        timesheet = _get_worker_timesheet_or_404(worker_profile, timesheet_id)
        return Response(TimesheetDetailSerializer(timesheet).data, status=status.HTTP_200_OK)

    def patch(self, request, timesheet_id):
        worker_profile = _require_worker_profile(request)
        timesheet = _get_worker_timesheet_or_404(worker_profile, timesheet_id)
        serializer = TimesheetWriteSerializer(data=request.data, context={"instance": timesheet}, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            timesheet = TimesheetService.update_worker_draft(
                timesheet=timesheet,
                worker_profile=worker_profile,
                user=request.user,
                attrs=dict(serializer.validated_data),
            )
        except TimesheetPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except TimesheetTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except TimesheetValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response(TimesheetDetailSerializer(timesheet).data, status=status.HTTP_200_OK)


class WorkerTimesheetSubmitView(APIView):
    permission_classes = [IsAuthenticated, IsWorkerProfile]

    def post(self, request, timesheet_id):
        worker_profile = _require_worker_profile(request)
        timesheet = _get_worker_timesheet_or_404(worker_profile, timesheet_id)
        serializer = TimesheetWriteSerializer(data=request.data or {}, context={"instance": timesheet}, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            timesheet = TimesheetService.submit_for_worker(
                timesheet=timesheet,
                worker_profile=worker_profile,
                user=request.user,
                attrs=dict(serializer.validated_data),
            )
        except TimesheetPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except TimesheetTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except TimesheetValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response(TimesheetDetailSerializer(timesheet).data, status=status.HTTP_200_OK)


class WorkerTimesheetCostAllocationView(APIView):
    permission_classes = [IsAuthenticated, IsWorkerProfile]

    def post(self, request):
        worker_profile = _require_worker_profile(request)
        serializer = CostAllocationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            assignments = TimesheetService.assign_cost_allocations(
                worker_profile=worker_profile,
                engagement_id=serializer.validated_data["worker_engagement_id"],
                tasks=serializer.validated_data["tasks"],
            )
        except TimesheetValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"assignments": assignments}, status=status.HTTP_200_OK)


class TenantTimesheetListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        request.membership = get_active_tenant_membership(request)
        queryset = TimesheetService.tenant_queryset(request)
        queryset = _apply_timesheet_filters(queryset, request)
        page_data = _paginate_queryset(request, queryset, self.DEFAULT_PAGE_SIZE, self.MAX_PAGE_SIZE)
        return Response(
            {
                "results": TimesheetListSerializer(page_data["records"], many=True).data,
                "pagination": page_data["pagination"],
            },
            status=status.HTTP_200_OK,
        )


class TenantTimesheetDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES

    def get(self, request, timesheet_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        request.membership = get_active_tenant_membership(request)
        timesheet = get_object_or_404(TimesheetService.tenant_queryset(request), pk=timesheet_id)
        return Response(TimesheetDetailSerializer(timesheet).data, status=status.HTTP_200_OK)


class TenantTimesheetApproveView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = APPROVER_ROLES

    def post(self, request, timesheet_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        membership = get_active_tenant_membership(request)
        timesheet = get_object_or_404(TimesheetService.tenant_queryset(request), pk=timesheet_id)
        try:
            timesheet = TimesheetService.approve(
                timesheet=timesheet,
                user=request.user,
                membership=membership,
            )
        except TimesheetPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except TimesheetTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(TimesheetDetailSerializer(timesheet).data, status=status.HTTP_200_OK)


class TenantTimesheetRejectView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = APPROVER_ROLES

    def post(self, request, timesheet_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        membership = get_active_tenant_membership(request)
        timesheet = get_object_or_404(TimesheetService.tenant_queryset(request), pk=timesheet_id)
        serializer = TimesheetDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            timesheet = TimesheetService.reject(
                timesheet=timesheet,
                user=request.user,
                membership=membership,
                rejection_reason=serializer.validated_data.get("rejection_reason", ""),
            )
        except TimesheetPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except TimesheetTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(TimesheetDetailSerializer(timesheet).data, status=status.HTTP_200_OK)


def _require_worker_profile(request):
    worker_profile = get_active_worker_profile(request.user)
    if not worker_profile:
        raise PermissionDenied("Worker profile is required.")
    return worker_profile


def _get_worker_timesheet_or_404(worker_profile, timesheet_id):
    return get_object_or_404(TimesheetService.worker_queryset(worker_profile), pk=timesheet_id)


def _ensure_tenant_context(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)
    return None


def _apply_timesheet_filters(queryset, request):
    status_param = (request.GET.get("status") or "").strip().lower()
    if status_param:
        queryset = queryset.filter(status=status_param)

    engagement_param = (request.GET.get("worker_engagement_id") or "").strip()
    if engagement_param:
        queryset = queryset.filter(worker_engagement_id=engagement_param)

    period_start = (request.GET.get("period_start") or "").strip()
    if period_start:
        queryset = queryset.filter(period_start=period_start)

    return queryset


def _paginate_queryset(request, queryset, default_page_size, max_page_size):
    page = _parse_positive_int(request.GET.get("page"), default=1, field_name="page")
    page_size = _parse_positive_int(request.GET.get("page_size"), default=default_page_size, field_name="page_size")
    page_size = min(page_size, max_page_size)

    paginator = Paginator(queryset.order_by("-period_start", "-id"), page_size)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else None

    return {
        "records": list(page_obj.object_list) if page_obj is not None else [],
        "pagination": {
            "page": page_obj.number if page_obj is not None else 1,
            "page_size": page_size,
            "total_count": paginator.count,
            "total_pages": paginator.num_pages,
            "has_next": bool(page_obj and page_obj.has_next()),
            "has_previous": bool(page_obj and page_obj.has_previous()),
        },
    }


def _parse_positive_int(raw_value, *, default, field_name):
    if raw_value in (None, ""):
        return default
    try:
        parsed = int(str(raw_value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError({field_name: "Must be a positive integer."}) from exc
    if parsed < 1:
        raise ValidationError({field_name: "Must be greater than or equal to 1."})
    return parsed
