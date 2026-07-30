from django.core.paginator import EmptyPage, Paginator
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership
from apps.common.permissions import HasRole, IsTenantMember
from apps.workorders.models import WorkOrder
from apps.workorders.serializers import (
    WorkOrderDecisionSerializer,
    WorkOrderDetailSerializer,
    WorkOrderListSerializer,
    WorkOrderSupplierChangeRequestSerializer,
    WorkOrderSupplierDecisionSerializer,
    WorkOrderWriteSerializer,
)
from apps.workorders.services import (
    WorkOrderPermissionError,
    WorkOrderService,
    WorkOrderTransitionError,
    WorkOrderValidationError,
)
from apps.workers.services import (
    InviteDeliveryError,
    LifecycleConfigurationError,
    LifecycleTransitionError,
    WorkOrderSupplierService,
)


EDITOR_ROLES = [
    Membership.ROLE_ADMIN,
    Membership.ROLE_BUSINESS,
    Membership.ROLE_MANAGER,
]

DECISION_ROLES = [
    Membership.ROLE_ADMIN,
    Membership.ROLE_FINANCE,
    Membership.ROLE_MANAGER,
]


class WorkOrderListCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        membership = _get_membership(request)
        queryset = _base_queryset(request)

        status_param = (request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        approval_status_param = (request.GET.get("approval_status") or "").strip().lower()
        if approval_status_param:
            queryset = queryset.filter(approval_status=approval_status_param)

        intake_param = (request.GET.get("intake") or "").strip()
        if intake_param:
            queryset = queryset.filter(intake_id=intake_param)

        supplier_param = (request.GET.get("supplier") or "").strip()
        if supplier_param:
            queryset = queryset.filter(supplier_id=supplier_param)

        mine = (request.GET.get("mine") or "").strip().lower()
        if mine in {"1", "true", "yes"}:
            queryset = queryset.filter(created_by=request.user)

        if membership and membership.role == Membership.ROLE_SUPPLIER:
            queryset = queryset.filter(supplier_id=membership.supplier_id)

        page = _parse_positive_int(request.GET.get("page"), default=1, field_name="page")
        page_size = _parse_positive_int(
            request.GET.get("page_size"),
            default=self.DEFAULT_PAGE_SIZE,
            field_name="page_size",
        )
        page_size = min(page_size, self.MAX_PAGE_SIZE)

        paginator = Paginator(queryset.order_by("-created_at", "-id"), page_size)
        try:
            page_obj = paginator.page(page)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else None

        records = list(page_obj.object_list) if page_obj is not None else []
        data = WorkOrderListSerializer(records, many=True).data
        return Response(
            {
                "results": data,
                "pagination": {
                    "page": page_obj.number if page_obj is not None else 1,
                    "page_size": page_size,
                    "total_count": paginator.count,
                    "total_pages": paginator.num_pages,
                    "has_next": bool(page_obj and page_obj.has_next()),
                    "has_previous": bool(page_obj and page_obj.has_previous()),
                },
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        _require_roles(request, EDITOR_ROLES)
        serializer = WorkOrderWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        attrs = dict(serializer.validated_data)

        try:
            work_order = WorkOrderService.create_draft(
                tenant=request.tenant,
                user=request.user,
                attrs=attrs,
            )
        except WorkOrderValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            _serialize_detail(request, work_order),
            status=status.HTTP_201_CREATED,
        )


class WorkOrderDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request, work_order_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        work_order = _get_work_order_or_404(request, work_order_id)
        _assert_work_order_access(request, work_order)

        return Response(
            _serialize_detail(request, work_order),
            status=status.HTTP_200_OK,
        )

    def patch(self, request, work_order_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        _require_roles(request, EDITOR_ROLES)
        work_order = _get_work_order_or_404(request, work_order_id)
        _assert_work_order_access(request, work_order)

        serializer = WorkOrderWriteSerializer(work_order, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        attrs = dict(serializer.validated_data)

        try:
            updated = WorkOrderService.update_draft(
                tenant=request.tenant,
                user=request.user,
                work_order=work_order,
                attrs=attrs,
            )
        except WorkOrderTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except WorkOrderValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            _serialize_detail(request, updated),
            status=status.HTTP_200_OK,
        )


class WorkOrderSubmitView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = EDITOR_ROLES

    def post(self, request, work_order_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        work_order = _get_work_order_or_404(request, work_order_id)
        _assert_work_order_access(request, work_order)

        try:
            work_order = WorkOrderService.submit(
                tenant=request.tenant,
                user=request.user,
                work_order=work_order,
            )
        except WorkOrderTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except WorkOrderValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            _serialize_detail(request, work_order),
            status=status.HTTP_200_OK,
        )


class WorkOrderApproveView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = DECISION_ROLES

    def post(self, request, work_order_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        work_order = _get_work_order_or_404(request, work_order_id)
        _assert_work_order_access(request, work_order, decision=True)

        serializer = WorkOrderDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        try:
            work_order = WorkOrderService.approve(
                tenant=request.tenant,
                user=request.user,
                work_order=work_order,
                decision_reason=serializer.validated_data.get("decision_reason", ""),
            )
        except WorkOrderTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except WorkOrderPermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except WorkOrderValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            _serialize_detail(request, work_order),
            status=status.HTTP_200_OK,
        )


class WorkOrderRejectView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = DECISION_ROLES

    def post(self, request, work_order_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        work_order = _get_work_order_or_404(request, work_order_id)
        _assert_work_order_access(request, work_order, decision=True)

        serializer = WorkOrderDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        try:
            work_order = WorkOrderService.reject(
                tenant=request.tenant,
                user=request.user,
                work_order=work_order,
                decision_reason=serializer.validated_data.get("decision_reason", ""),
            )
        except WorkOrderTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except WorkOrderPermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        return Response(
            _serialize_detail(request, work_order),
            status=status.HTTP_200_OK,
        )


class WorkOrderSupplierAcceptView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, work_order_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        work_order = _get_work_order_or_404(request, work_order_id)
        _assert_supplier_work_order_action(request, work_order)
        serializer = WorkOrderSupplierDecisionSerializer(
            data=request.data or {}
        )
        serializer.is_valid(raise_exception=True)
        try:
            acceptance = WorkOrderSupplierService.accept(
                tenant=request.tenant,
                user=request.user,
                work_order=work_order,
                supplier_response_notes=serializer.validated_data.get(
                    "supplier_response_notes",
                    "",
                ),
                base_url=request.build_absolute_uri("/").rstrip("/"),
            )
        except LifecycleConfigurationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except LifecycleTransitionError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except InviteDeliveryError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = _serialize_detail(request, acceptance.work_order)
        data.update(
            {
                "worker_id": acceptance.worker.id,
                "worker_is_new": acceptance.worker_is_new,
                "worker_assignment_id": acceptance.worker_engagement.id,
                "onboarding_run_id": acceptance.onboarding_run.id,
                "matched_workflow_id": acceptance.onboarding_run.workflow_id,
                "registration_required": acceptance.registration_required,
            }
        )
        return Response(data, status=status.HTTP_200_OK)


class WorkOrderSupplierRequestChangeView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, work_order_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        work_order = _get_work_order_or_404(request, work_order_id)
        _assert_supplier_work_order_action(request, work_order)
        serializer = WorkOrderSupplierChangeRequestSerializer(
            data=request.data or {}
        )
        serializer.is_valid(raise_exception=True)
        try:
            work_order = WorkOrderSupplierService.request_change(
                tenant=request.tenant,
                user=request.user,
                work_order=work_order,
                notes=serializer.validated_data["supplier_response_notes"],
            )
        except LifecycleTransitionError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            _serialize_detail(request, work_order),
            status=status.HTTP_200_OK,
        )


def _base_queryset(request):
    return WorkOrder.objects.filter(tenant_id=request.tenant.id).select_related(
        "intake",
        "selected_candidate",
        "supplier",
        "role_definition",
        "cost_center",
        "legal_entity",
        "site",
        "approval_chain",
        "submitted_by",
        "decided_by",
        "created_by",
        "engagement",
    )


def _get_work_order_or_404(request, work_order_id):
    return get_object_or_404(_base_queryset(request), pk=work_order_id)


def _ensure_tenant_context(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)
    return None


def _get_membership(request):
    return Membership.objects.filter(
        user=request.user,
        tenant_id=request.tenant.id,
        status=Membership.STATUS_ACTIVE,
        is_active=True,
    ).first()


def _serialize_detail(request, work_order):
    data = WorkOrderDetailSerializer(work_order).data
    approval_runtime = _build_approval_runtime(work_order)
    data["approval_runtime"] = approval_runtime
    data["permissions"] = _build_permissions(
        request,
        work_order,
        approval_runtime=approval_runtime,
    )
    return data


def _build_permissions(request, work_order, *, approval_runtime):
    membership = _get_membership(request)
    current_approver_id = approval_runtime.get("current_approver_id")
    can_approve = bool(
        membership
        and membership.role in DECISION_ROLES
        and work_order.status == WorkOrder.STATUS_SUBMITTED
        and work_order.approval_status == WorkOrder.APPROVAL_PROCESSING
        and current_approver_id == request.user.id
    )

    can_respond_to_work_order = bool(
        membership
        and membership.role == Membership.ROLE_SUPPLIER
        and membership.supplier_id
        and membership.supplier_id == work_order.supplier_id
        and work_order.status == WorkOrder.STATUS_APPROVED
        and work_order.supplier_acceptance_status
        == WorkOrder.SUPPLIER_ACCEPTANCE_PENDING
    )
    return {
        "can_approve": can_approve,
        "can_reject": can_approve,
        "can_respond_to_work_order": can_respond_to_work_order,
    }


def _assert_supplier_work_order_action(request, work_order):
    membership = _get_membership(request)
    if (
        not membership
        or membership.role != Membership.ROLE_SUPPLIER
        or not membership.supplier_id
        or membership.supplier_id != work_order.supplier_id
    ):
        raise PermissionDenied()


def _require_roles(request, roles):
    membership = _get_membership(request)
    if membership and membership.role in set(roles):
        return membership
    raise PermissionDenied()


def _assert_work_order_access(request, work_order, decision=False):
    membership = _get_membership(request)
    if not membership:
        raise PermissionDenied()

    if membership.role == Membership.ROLE_SUPPLIER:
        if not membership.supplier_id or membership.supplier_id != work_order.supplier_id:
            raise PermissionDenied()
        if decision:
            raise PermissionDenied()
        return

    # Non-supplier users can read tenant work orders. Decision authority is enforced by role + service checks.


def _build_approval_runtime(work_order):
    snapshot = work_order.approval_chain_snapshot or {}
    resolved_steps = snapshot.get("resolved_steps") or []
    current_sequence = snapshot.get("current_step_sequence")
    current_step = None
    if current_sequence is not None:
        for step in resolved_steps:
            if step.get("sequence") == current_sequence:
                current_step = step
                break
    if current_step is None:
        for step in sorted(resolved_steps, key=lambda item: item.get("sequence") or 0):
            if step.get("status") not in {"approved", "rejected"}:
                current_step = step
                break

    approvals_remaining = snapshot.get("approvals_remaining")
    if approvals_remaining is None and isinstance(resolved_steps, list):
        approvals_remaining = sum(1 for step in resolved_steps if step.get("status") not in {"approved", "rejected"})

    return {
        "current_approver_id": current_step.get("approver_id") if current_step else None,
        "current_approver_name": current_step.get("approver_name") if current_step else None,
        "current_step_sequence": current_step.get("sequence") if current_step else None,
        "approvals_remaining": approvals_remaining or 0,
        "matched_chain_id": snapshot.get("approval_chain_id"),
        "matched_chain_name": snapshot.get("approval_chain_name"),
        "match_strategy": snapshot.get("match_strategy"),
        "computed_at": snapshot.get("resolved_at"),
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
