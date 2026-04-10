from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership
from apps.common.permissions import HasRole, IsTenantMember
from apps.intake.approval import compute_approval_preview
from apps.intake.models import IntakeRequest
from apps.intake.serializers import (
    IntakeDecisionSerializer,
    IntakeRequestDetailSerializer,
    IntakeRequestWriteSerializer,
    NovaConfidenceRequestSerializer,
)
from apps.intake.services import IntakeService, IntakeTransitionError, IntakeValidationError
from apps.intake.validation import validate_intake_request


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


class IntakeDraftCreateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = EDITOR_ROLES

    def post(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        serializer = IntakeRequestWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        intake = IntakeService.create_draft(tenant=request.tenant, user=request.user, attrs=serializer.validated_data)
        data = IntakeRequestDetailSerializer(intake).data
        data["warnings"] = validate_intake_request(intake, strict=False)
        return Response(data, status=status.HTTP_201_CREATED)


class IntakeListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        queryset = IntakeRequest.objects.all().order_by("-created_at")
        status_param = (request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        mine = (request.GET.get("mine") or "").strip().lower()
        if mine in {"1", "true", "yes"}:
            queryset = queryset.filter(created_by=request.user)

        data = IntakeRequestDetailSerializer(queryset[:100], many=True).data
        return Response({"results": data}, status=status.HTTP_200_OK)


class IntakeDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(intake_id)
        data = IntakeRequestDetailSerializer(intake).data
        data["validation"] = {"warnings": validate_intake_request(intake, strict=False)}
        data["approval_preview"] = compute_approval_preview(intake)
        return Response(data, status=status.HTTP_200_OK)

    def patch(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(intake_id)
        _require_roles(request, EDITOR_ROLES)

        serializer = IntakeRequestWriteSerializer(intake, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        try:
            intake, warnings = IntakeService.update_draft(
                tenant=request.tenant,
                user=request.user,
                intake=intake,
                attrs=serializer.validated_data,
            )
        except IntakeTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        data = IntakeRequestDetailSerializer(intake).data
        data["warnings"] = warnings
        return Response(data, status=status.HTTP_200_OK)


class IntakeSubmitView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = EDITOR_ROLES

    def post(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(intake_id)
        try:
            intake = IntakeService.submit(tenant=request.tenant, user=request.user, intake=intake)
        except IntakeValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        except IntakeTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        data = IntakeRequestDetailSerializer(intake).data
        data["approval_preview"] = compute_approval_preview(intake)
        return Response(data, status=status.HTTP_200_OK)


class IntakeApproveView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = DECISION_ROLES

    def post(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(intake_id)
        serializer = IntakeDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        try:
            intake = IntakeService.approve(
                tenant=request.tenant,
                user=request.user,
                intake=intake,
                decision_reason=serializer.validated_data.get("decision_reason", ""),
            )
        except IntakeTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(IntakeRequestDetailSerializer(intake).data, status=status.HTTP_200_OK)


class IntakeRejectView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = DECISION_ROLES

    def post(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(intake_id)
        serializer = IntakeDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        try:
            intake = IntakeService.reject(
                tenant=request.tenant,
                user=request.user,
                intake=intake,
                decision_reason=serializer.validated_data.get("decision_reason", ""),
            )
        except IntakeTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(IntakeRequestDetailSerializer(intake).data, status=status.HTTP_200_OK)


class IntakeApprovalPreviewView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(intake_id)
        preview = compute_approval_preview(intake)
        return Response({"approval_preview": preview}, status=status.HTTP_200_OK)


class NovaIntakeConfidenceView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        serializer = NovaConfidenceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        intake = _get_intake_or_404(serializer.validated_data["intake_id"])
        submit_errors = validate_intake_request(intake, strict=True)
        missing_fields = []
        for error in submit_errors:
            if error["code"] == "required":
                missing_fields.append({"field": error["field"], "reason": error["message"]})

        weak_fields = []
        if intake.description and len(intake.description.strip()) < 40:
            weak_fields.append(
                {
                    "field": "description",
                    "reason": "Description is too short for reliable review.",
                    "suggestion": "Provide scope, outcomes, and required skills.",
                }
            )
        if intake.decision_reason == "" and intake.status in {
            IntakeRequest.STATUS_APPROVED,
            IntakeRequest.STATUS_REJECTED,
        }:
            weak_fields.append(
                {
                    "field": "decision_reason",
                    "reason": "Decision rationale is empty.",
                    "suggestion": "Capture concise rationale for auditability.",
                }
            )

        penalty = (0.2 * len(missing_fields)) + (0.08 * len(weak_fields))
        confidence_score = max(0.0, min(1.0, round(1.0 - penalty, 2)))

        return Response(
            {
                "missing_fields": missing_fields,
                "weak_fields": weak_fields,
                "confidence_score": confidence_score,
                "recommended_approval_route": compute_approval_preview(intake),
            },
            status=status.HTTP_200_OK,
        )


class ApprovalsDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        membership = _get_membership(request)
        submitted_qs = (
            IntakeRequest.objects.filter(status=IntakeRequest.STATUS_SUBMITTED)
            .select_related("created_by", "supplier")
            .order_by("-submitted_at", "-created_at")
        )

        my_pending_qs = submitted_qs.filter(created_by=request.user)[:100]
        my_pending_requests = [_serialize_approval_item(intake) for intake in my_pending_qs]

        my_approval_queue = []
        if membership and membership.role in DECISION_ROLES:
            for intake in submitted_qs.exclude(created_by=request.user)[:200]:
                if _role_can_approve_intake(membership.role, intake):
                    my_approval_queue.append(_serialize_approval_item(intake))

        return Response(
            {
                "my_pending_requests": my_pending_requests,
                "my_approval_queue": my_approval_queue,
                "meta": {
                    "is_approver": bool(membership and membership.role in DECISION_ROLES),
                    "role": membership.role if membership else None,
                },
            },
            status=status.HTTP_200_OK,
        )


def _get_intake_or_404(intake_id):
    return get_object_or_404(IntakeRequest.objects.all(), pk=intake_id)


def _require_roles(request, roles):
    if request.tenant.schema_name == "public":
        return

    membership = Membership.objects.filter(
        user=request.user,
        tenant_id=request.tenant.id,
        status=Membership.STATUS_ACTIVE,
        is_active=True,
    ).first()
    if membership and membership.role in set(roles):
        return

    raise PermissionDenied()


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


def _role_can_approve_intake(role, intake):
    if role == Membership.ROLE_ADMIN:
        return True

    preview = compute_approval_preview(intake)
    preview_groups = {
        str(step.get("approver_group", "")).replace(" ", "").lower()
        for step in preview
    }

    if role == Membership.ROLE_FINANCE:
        return "finance" in preview_groups

    if role == Membership.ROLE_MANAGER:
        return bool(preview_groups & {"hiringmanager", "procurement", "manager"})

    return False


def _serialize_approval_item(intake):
    return {
        "intake_id": intake.id,
        "request_id": _format_request_id(intake),
        "type": _request_type_label(intake),
        "title": intake.title or "(Untitled request)",
        "requested_by": _display_user(intake.created_by),
        "supplier": intake.supplier.name if intake.supplier else None,
        "amount": _format_amount(intake),
        "approval_type": _approval_type_label(intake),
        "status": intake.status,
        "submitted_at": intake.submitted_at,
        "submitted_ago": _relative_time(intake.submitted_at),
    }


def _format_request_id(intake):
    year = intake.created_at.year if intake.created_at else timezone.now().year
    return f"APR-{year}-{intake.id:03d}"


def _request_type_label(intake):
    if intake.engagement_type == IntakeRequest.ENGAGEMENT_SOW:
        return "Statement of Work"
    if intake.engagement_type == IntakeRequest.ENGAGEMENT_STAFFING:
        return "Job Posting"
    return "Request"


def _approval_type_label(intake):
    if intake.engagement_type == IntakeRequest.ENGAGEMENT_STAFFING:
        return "Rate Approval"
    if intake.engagement_type == IntakeRequest.ENGAGEMENT_SOW:
        return "Financial"
    return "General"


def _display_user(user):
    if not user:
        return None
    full_name = user.get_full_name().strip()
    return full_name or user.username


def _format_amount(intake):
    if intake.engagement_type == IntakeRequest.ENGAGEMENT_STAFFING and intake.target_rate is not None:
        unit = "hr" if intake.rate_unit == IntakeRequest.RATE_HOURLY else "day"
        return f"{_format_money(intake.target_rate, intake.currency)}/{unit}"

    if intake.budget_amount is not None:
        return _format_money(intake.budget_amount, intake.currency)

    return None


def _format_money(value, currency):
    try:
        amount = Decimal(value)
    except Exception:
        return None

    amount_text = f"{amount:,.2f}"
    if (currency or "").upper() == "USD":
        return f"${amount_text}"
    return f"{(currency or '').upper()} {amount_text}".strip()


def _relative_time(dt):
    if not dt:
        return None

    delta = timezone.now() - dt
    if delta.days >= 1:
        return f"{delta.days} day{'s' if delta.days != 1 else ''} ago"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    minutes = max(1, delta.seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
