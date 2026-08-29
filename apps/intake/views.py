from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.core.paginator import EmptyPage, Paginator
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership
from apps.accounts.profile import NON_WORKER_TENANT_ROLES
from apps.common.permissions import HasRole, IsTenantMember
from apps.intake.approval import compute_approval_preview
from apps.intake.models import IntakeRequest
from apps.intake.serializers import (
    IntakeDecisionSerializer,
    IntakeRequestDetailSerializer,
    IntakeSelectedCandidateSerializer,
    IntakeRequestWriteSerializer,
    NovaConfidenceRequestSerializer,
)
from apps.intake.services import (
    IntakePermissionError,
    IntakeService,
    IntakeTransitionError,
    IntakeValidationError,
)
from apps.intake.validation import validate_intake_request
from apps.rates.pricing import resolve_intake_rate_card_pricing, serialize_pricing_payload


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

READ_ROLES = list(NON_WORKER_TENANT_ROLES)


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
        data = _attach_intake_computed_fields(intake, data)
        data["warnings"] = validate_intake_request(intake, strict=False)
        return Response(data, status=status.HTTP_201_CREATED)


class IntakeListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        queryset = IntakeRequest.objects.filter(tenant_id=request.tenant.id).prefetch_related("qualifications").order_by("-created_at")
        status_param = (request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        mine = (request.GET.get("mine") or "").strip().lower()
        if mine in {"1", "true", "yes"}:
            queryset = queryset.filter(created_by=request.user)

        page = _parse_positive_int(request.GET.get("page"), default=1, field_name="page")
        page_size = _parse_positive_int(
            request.GET.get("page_size"),
            default=self.DEFAULT_PAGE_SIZE,
            field_name="page_size",
        )
        page_size = min(page_size, self.MAX_PAGE_SIZE)

        paginator = Paginator(queryset, page_size)
        try:
            page_obj = paginator.page(page)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else None

        records = list(page_obj.object_list) if page_obj is not None else []
        data = IntakeRequestDetailSerializer(records, many=True).data
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


class IntakeDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES

    def get(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
        data = IntakeRequestDetailSerializer(intake).data
        data = _attach_intake_computed_fields(intake, data)
        data["validation"] = {"warnings": validate_intake_request(intake, strict=False)}
        data["approval_preview"] = compute_approval_preview(intake)
        data["approval_runtime"] = _build_approval_runtime(intake)
        return Response(data, status=status.HTTP_200_OK)

    def patch(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
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
        data = _attach_intake_computed_fields(intake, data)
        data["warnings"] = warnings
        return Response(data, status=status.HTTP_200_OK)


class IntakeSubmitView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = EDITOR_ROLES

    def post(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
        try:
            intake = IntakeService.submit(tenant=request.tenant, user=request.user, intake=intake)
        except IntakeValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)
        except IntakeTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        data = IntakeRequestDetailSerializer(intake).data
        data = _attach_intake_computed_fields(intake, data)
        data["approval_preview"] = compute_approval_preview(intake)
        data["approval_runtime"] = _build_approval_runtime(intake)
        return Response(data, status=status.HTTP_200_OK)


class IntakeApproveView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = DECISION_ROLES

    def post(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
        serializer = IntakeDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        try:
            intake = IntakeService.approve(
                tenant=request.tenant,
                user=request.user,
                intake=intake,
                decision_reason=serializer.validated_data.get("decision_reason", ""),
                portal_base_url=request.build_absolute_uri("/").rstrip("/"),
            )
        except IntakeTransitionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except IntakePermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except IntakeValidationError as exc:
            return Response({"errors": exc.errors}, status=status.HTTP_400_BAD_REQUEST)

        data = IntakeRequestDetailSerializer(intake).data
        data = _attach_intake_computed_fields(intake, data)
        data["approval_preview"] = compute_approval_preview(intake)
        data["approval_runtime"] = _build_approval_runtime(intake)
        return Response(data, status=status.HTTP_200_OK)


class IntakeRejectView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = DECISION_ROLES

    def post(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
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
        except IntakePermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        data = IntakeRequestDetailSerializer(intake).data
        data = _attach_intake_computed_fields(intake, data)
        data["approval_preview"] = compute_approval_preview(intake)
        data["approval_runtime"] = _build_approval_runtime(intake)
        return Response(data, status=status.HTTP_200_OK)


class IntakeApprovalPreviewView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES

    def get(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
        preview = compute_approval_preview(intake)
        return Response({"approval_preview": preview}, status=status.HTTP_200_OK)


class IntakeSelectedCandidateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES

    def get(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
        membership = _get_membership(request)
        if not membership:
            raise PermissionDenied()

        if membership.role == Membership.ROLE_SUPPLIER:
            if not membership.supplier_id or membership.supplier_id != intake.supplier_id:
                raise PermissionDenied()
            queryset = intake.selected_candidates.filter(supplier_id=membership.supplier_id)
        else:
            queryset = intake.selected_candidates.all()

        return Response(
            {"results": IntakeSelectedCandidateSerializer(queryset, many=True).data},
            status=status.HTTP_200_OK,
        )

    def post(self, request, intake_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        intake = _get_intake_or_404(request, intake_id)
        membership = _get_membership(request)
        if not membership or membership.role != Membership.ROLE_SUPPLIER:
            raise PermissionDenied()
        if not membership.supplier_id or membership.supplier_id != intake.supplier_id:
            raise PermissionDenied()

        if intake.status != IntakeRequest.STATUS_APPROVED or intake.approval_status != "approved":
            return Response(
                {"detail": "Candidate submissions are available only after the job posting is fully approved."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = IntakeSelectedCandidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        candidate = serializer.save(
            intake=intake,
            supplier_id=membership.supplier_id,
            submitted_by=request.user,
        )
        return Response(
            IntakeSelectedCandidateSerializer(candidate).data,
            status=status.HTTP_201_CREATED,
        )


class NovaIntakeConfidenceView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES

    def post(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        serializer = NovaConfidenceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        intake = _get_intake_or_404(request, serializer.validated_data["intake_id"])
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
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = READ_ROLES

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        membership = _get_membership(request)
        submitted_qs = (
            IntakeRequest.objects.filter(
                status=IntakeRequest.STATUS_SUBMITTED,
                tenant_id=request.tenant.id,
            )
            .select_related("created_by", "supplier")
            .order_by("-submitted_at", "-created_at")
        )

        my_pending_qs = submitted_qs.filter(created_by=request.user)[:100]
        my_pending_requests = [_serialize_approval_item(intake) for intake in my_pending_qs]

        my_approval_queue = []
        if membership and membership.role in DECISION_ROLES:
            queue_queryset = submitted_qs
            if membership.role != Membership.ROLE_ADMIN:
                queue_queryset = queue_queryset.exclude(created_by=request.user)

            for intake in queue_queryset[:200]:
                if _role_can_approve_intake(membership.role, intake, request.user):
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


def _get_intake_or_404(request, intake_id):
    return get_object_or_404(
        IntakeRequest.objects.select_related(
            "created_by",
            "submitted_by",
            "decided_by",
            "supplier",
            "site",
            "cost_center",
            "role_definition",
            "legal_entity",
            "rate_card",
            "approval_chain",
        ).prefetch_related("qualifications", "selected_candidates"),
        pk=intake_id,
        tenant_id=request.tenant.id,
    )


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


def _role_can_approve_intake(role, intake, user):
    if role == Membership.ROLE_ADMIN:
        return True

    snapshot = intake.approval_chain_snapshot or {}
    resolved_steps = snapshot.get("resolved_steps")
    if isinstance(resolved_steps, list) and resolved_steps:
        current_sequence = snapshot.get("current_step_sequence")
        current_step = None
        if current_sequence is not None:
            for step in sorted(resolved_steps, key=lambda item: item.get("sequence") or 0):
                if step.get("sequence") == current_sequence:
                    current_step = step
                    break
        if current_step is None:
            for step in sorted(resolved_steps, key=lambda item: item.get("sequence") or 0):
                if step.get("status") not in {"approved", "rejected"}:
                    current_step = step
                    break

        if user and current_step and int(current_step.get("approver_id") or 0) == user.id:
            return True

    preview_groups = {
        str(step.get("approver_group", "")).replace(" ", "").lower()
        for step in compute_approval_preview(intake)
    }

    if role == Membership.ROLE_FINANCE:
        return "finance" in preview_groups

    if role == Membership.ROLE_MANAGER:
        return bool(preview_groups & {"hiringmanager", "procurement", "manager"})

    return False


def _build_approval_runtime(intake):
    snapshot = intake.approval_chain_snapshot or {}
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


def _attach_intake_computed_fields(intake, data):
    data["supplier_name"] = intake.supplier.name if intake.supplier else None
    data["role_name"] = intake.role_definition.name if intake.role_definition else None
    data["site_name"] = intake.site.name if intake.site else None
    data["cost_center_name"] = intake.cost_center.name if intake.cost_center else None
    data["legal_entity_name"] = intake.legal_entity.name if intake.legal_entity else None
    data["work_location_label"] = _derive_work_location_label(intake)

    selected_candidate = _latest_selected_candidate(intake)
    if selected_candidate:
        data["selected_candidate"] = IntakeSelectedCandidateSerializer(selected_candidate).data
        data["pay_rate"] = (
            str(selected_candidate.proposed_rate)
            if selected_candidate.proposed_rate is not None
            else None
        )
    else:
        data["selected_candidate"] = None
        data["pay_rate"] = None

    pricing_context = resolve_intake_rate_card_pricing(
        intake=intake,
        supplier=intake.supplier,
        work_location_label=data["work_location_label"],
        strict=True,
    )
    if pricing_context:
        pricing_payload = serialize_pricing_payload(pricing_context)
        data["rate_card_pricing"] = pricing_payload
        data["bill_rate"] = pricing_payload.get("bill_rate")
        data["markup_percent"] = pricing_payload.get("total_percent_markup")
        data["base_rate"] = pricing_payload.get("base_amount")
    else:
        data["rate_card_pricing"] = None
        data["bill_rate"] = str(intake.target_rate) if intake.target_rate is not None else None
        data["markup_percent"] = None
        data["base_rate"] = str(intake.target_rate) if intake.target_rate is not None else None

    return data


def _latest_selected_candidate(intake):
    prefetched = getattr(intake, "_prefetched_objects_cache", {}) or {}
    selected = prefetched.get("selected_candidates")
    if selected is not None:
        if not selected:
            return None
        return sorted(selected, key=lambda row: (row.created_at, row.id), reverse=True)[0]

    return intake.selected_candidates.order_by("-created_at", "-id").first()


def _derive_work_location_label(intake):
    if intake.site:
        return ", ".join(
            part
            for part in [
                intake.site.city,
                intake.site.state_province,
                intake.site.country,
            ]
            if part
        ) or intake.site.name
    return ", ".join(part for part in [intake.city, intake.state_province, intake.country] if part)


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
