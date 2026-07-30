from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.paginator import EmptyPage, Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership
from apps.accounts.password_policy import (
    record_password_history,
    validate_password_policy,
)
from apps.common.permissions import IsTenantMember
from apps.workers.models import (
    Engagement,
    LifecycleActivity,
    LifecycleBlock,
    LifecycleRun,
    Worker,
    WorkerEngagement,
    WorkerInvite,
)
from apps.workers.permissions import (
    INTERNAL_EDIT_ROLES,
    can_access_worker,
    can_manage_worker,
    can_update_activity,
    get_membership,
)
from apps.workers.serializers import (
    EngagementDecisionSerializer,
    EngagementDetailSerializer,
    EngagementListSerializer,
    EngagementRequestChangeSerializer,
    LifecycleActivityUpdateSerializer,
    WorkerContractExtensionSerializer,
    WorkerRegisterSerializer,
    lifecycle_detail,
    lifecycle_summary,
    worker_directory_record,
)
from apps.workers.services import (
    EngagementService,
    InviteDeliveryError,
    LifecycleConfigurationError,
    LifecycleService,
    LifecycleTransitionError,
    WorkerContractService,
    WorkerInviteService,
)


User = get_user_model()


class EngagementListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    DEFAULT_PAGE_SIZE = 25
    MAX_PAGE_SIZE = 100

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        queryset = _engagement_queryset(request)
        membership = get_membership(request)
        if membership and membership.role == Membership.ROLE_SUPPLIER:
            queryset = queryset.filter(
                work_order__supplier_id=membership.supplier_id
            )
        elif membership and membership.role == Membership.ROLE_WORKER:
            queryset = queryset.filter(
                worker_engagement__worker__user=request.user
            )

        status_param = (request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)
        supplier_param = (request.GET.get("supplier") or "").strip()
        if supplier_param.isdigit():
            queryset = queryset.filter(work_order__supplier_id=supplier_param)
        work_order_param = (request.GET.get("work_order") or "").strip()
        if work_order_param.isdigit():
            queryset = queryset.filter(work_order_id=work_order_param)

        page, page_size = _pagination_params(
            request,
            default_page_size=self.DEFAULT_PAGE_SIZE,
            max_page_size=self.MAX_PAGE_SIZE,
        )
        page_obj, paginator = _paginate(queryset, page, page_size)
        records = list(page_obj.object_list) if page_obj else []
        return Response(
            {
                "results": EngagementListSerializer(records, many=True).data,
                "pagination": _pagination_payload(
                    page_obj=page_obj,
                    paginator=paginator,
                    page_size=page_size,
                ),
            },
            status=status.HTTP_200_OK,
        )


class EngagementDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request, engagement_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        engagement = get_object_or_404(
            _engagement_queryset(request),
            pk=engagement_id,
        )
        _assert_engagement_access(request, engagement)
        return Response(
            EngagementDetailSerializer(engagement).data,
            status=status.HTTP_200_OK,
        )


class EngagementAcceptView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, engagement_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        engagement = get_object_or_404(
            _engagement_queryset(request),
            pk=engagement_id,
        )
        _assert_supplier_action_access(request, engagement)
        serializer = EngagementDecisionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            acceptance = EngagementService.accept(
                tenant=request.tenant,
                user=request.user,
                engagement=engagement,
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

        data = dict(EngagementDetailSerializer(acceptance.engagement).data)
        data.update(
            {
                "worker_id": acceptance.worker.id,
                "worker_is_new": acceptance.worker_is_new,
                "worker_engagement_id": acceptance.worker_engagement.id,
                "onboarding_run_id": acceptance.onboarding_run.id,
                "matched_workflow_id": acceptance.onboarding_run.workflow_id,
                "registration_required": acceptance.registration_required,
            }
        )
        return Response(data, status=status.HTTP_200_OK)


class EngagementRequestChangeView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, engagement_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        engagement = get_object_or_404(
            _engagement_queryset(request),
            pk=engagement_id,
        )
        _assert_supplier_action_access(request, engagement)
        serializer = EngagementRequestChangeSerializer(
            data=request.data or {}
        )
        serializer.is_valid(raise_exception=True)
        try:
            engagement = EngagementService.request_change(
                tenant=request.tenant,
                user=request.user,
                engagement=engagement,
                notes=serializer.validated_data["supplier_response_notes"],
            )
        except LifecycleTransitionError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            EngagementDetailSerializer(engagement).data,
            status=status.HTTP_200_OK,
        )


class WorkerDirectoryListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    DEFAULT_PAGE_SIZE = 50
    MAX_PAGE_SIZE = 200

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error

        accessible = _worker_directory_queryset(request)
        total_workers = accessible.count()
        compliance_alerts = (
            accessible.exclude(status=Worker.STATUS_OFFBOARDED)
            .filter(
                Q(
                    status__in=[
                        Worker.STATUS_INVITED,
                        Worker.STATUS_ONBOARDING,
                        Worker.STATUS_OFFBOARDING,
                    ]
                )
                | Q(
                    engagements__lifecycle_runs__status__in=[
                        LifecycleRun.STATUS_PENDING,
                        LifecycleRun.STATUS_IN_PROGRESS,
                        LifecycleRun.STATUS_BLOCKED,
                    ]
                )
            )
            .distinct()
            .count()
        )

        queryset = accessible
        worker_status = (request.GET.get("status") or "").strip().lower()
        valid_statuses = {choice[0] for choice in Worker.STATUS_CHOICES}
        if worker_status:
            if worker_status not in valid_statuses:
                return Response(
                    {"detail": "Unsupported worker status."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(status=worker_status)

        search = (request.GET.get("search") or "").strip()
        if search:
            search_query = (
                Q(full_name__icontains=search)
                | Q(email__icontains=search)
                | Q(
                    engagements__work_order__work_order_number__icontains=search
                )
                | Q(
                    engagements__work_order__role_definition__name__icontains=search
                )
                | Q(
                    engagements__work_order__supplier__name__icontains=search
                )
                | Q(
                    engagements__engagement__engagement_number__icontains=search
                )
                | Q(
                    engagements__engagement__work_order__work_order_number__icontains=search
                )
                | Q(
                    engagements__engagement__work_order__role_definition__name__icontains=search
                )
                | Q(
                    engagements__engagement__work_order__supplier__name__icontains=search
                )
            )
            cws_digits = "".join(
                character for character in search if character.isdigit()
            )
            if search.upper().startswith("CWS") and cws_digits:
                search_query |= Q(pk=int(cws_digits))
            queryset = queryset.filter(search_query).distinct()

        page, page_size = _pagination_params(
            request,
            default_page_size=self.DEFAULT_PAGE_SIZE,
            max_page_size=self.MAX_PAGE_SIZE,
        )
        page_obj, paginator = _paginate(
            queryset.order_by("full_name", "id"),
            page,
            page_size,
        )
        records = list(page_obj.object_list) if page_obj else []
        return Response(
            {
                "results": [
                    worker_directory_record(worker)
                    for worker in records
                ],
                "summary": {
                    "total_workers": total_workers,
                    "compliance_alerts": compliance_alerts,
                },
                "pagination": _pagination_payload(
                    page_obj=page_obj,
                    paginator=paginator,
                    page_size=page_size,
                ),
            },
            status=status.HTTP_200_OK,
        )


class WorkerDirectoryDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request, worker_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        worker = get_object_or_404(
            _worker_directory_queryset(request),
            pk=worker_id,
        )
        engagement_id = _request_engagement_id(request)
        work_order_id = _request_work_order_id(request)
        if engagement_id is not None and not any(
            assignment.engagement_id == engagement_id
            for assignment in worker.visible_assignments
        ):
            return Response(
                {"detail": "Worker assignment was not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if work_order_id is not None and not any(
            assignment.resolved_work_order
            and assignment.resolved_work_order.id == work_order_id
            for assignment in worker.visible_assignments
        ):
            return Response(
                {"detail": "Worker assignment was not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            worker_directory_record(
                worker,
                engagement_id=engagement_id,
                include_assignments=True,
                request=request,
                work_order_id=work_order_id,
            ),
            status=status.HTTP_200_OK,
        )


class WorkerContractExtendView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, worker_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        if not can_manage_worker(request):
            raise PermissionDenied()
        serializer = WorkerContractExtensionSerializer(
            data=request.data or {}
        )
        serializer.is_valid(raise_exception=True)
        worker = get_object_or_404(
            Worker.objects.filter(tenant_id=request.tenant.id),
            pk=worker_id,
        )
        assignment = get_object_or_404(
            WorkerEngagement.objects.filter(
                tenant_id=request.tenant.id,
                worker=worker,
            ),
            **(
                {
                    "work_order_id": serializer.validated_data[
                        "work_order_id"
                    ]
                }
                if serializer.validated_data.get("work_order_id")
                else {
                    "engagement_id": serializer.validated_data[
                        "engagement_id"
                    ]
                }
            ),
        )
        try:
            WorkerContractService.extend(
                tenant=request.tenant,
                user=request.user,
                worker_engagement=assignment,
                end_date=serializer.validated_data["end_date"],
                notes=serializer.validated_data.get("notes", ""),
            )
        except LifecycleTransitionError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )

        worker = get_object_or_404(
            _worker_directory_queryset(request),
            pk=worker_id,
        )
        return Response(
            worker_directory_record(
                worker,
                engagement_id=assignment.engagement_id,
                work_order_id=assignment.resolved_work_order.id,
                include_assignments=True,
                request=request,
            ),
            status=status.HTTP_200_OK,
        )


class WorkerLifecycleListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]
    DEFAULT_PAGE_SIZE = 50
    MAX_PAGE_SIZE = 200

    def get(self, request):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        lifecycle_type = (
            request.GET.get("lifecycle_type")
            or LifecycleRun.TYPE_ONBOARDING
        ).strip().lower()
        if lifecycle_type not in {
            LifecycleRun.TYPE_ONBOARDING,
            LifecycleRun.TYPE_OFFBOARDING,
        }:
            return Response(
                {"detail": "Unsupported lifecycle type."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = _lifecycle_queryset(request).filter(
            lifecycle_type=lifecycle_type
        )
        membership = get_membership(request)
        if membership and membership.role == Membership.ROLE_WORKER:
            queryset = queryset.filter(
                worker_engagement__worker__user=request.user
            )
        elif membership and membership.role == Membership.ROLE_SUPPLIER:
            queryset = queryset.filter(
                Q(
                    worker_engagement__work_order__supplier_id=(
                        membership.supplier_id
                    )
                )
                | Q(
                    worker_engagement__engagement__work_order__supplier_id=(
                        membership.supplier_id
                    )
                )
            )

        status_param = (request.GET.get("status") or "").strip().lower()
        status_aliases = {
            "ready": LifecycleRun.STATUS_COMPLETE,
            "in progress": LifecycleRun.STATUS_IN_PROGRESS,
            "blocked": LifecycleRun.STATUS_BLOCKED,
            "pending": LifecycleRun.STATUS_PENDING,
        }
        if status_param and status_param not in {"all", "*"}:
            queryset = queryset.filter(
                status=status_aliases.get(status_param, status_param)
            )

        search = (
            request.GET.get("search")
            or request.GET.get("q")
            or ""
        ).strip()
        if search:
            queryset = queryset.filter(
                Q(worker_engagement__worker__full_name__icontains=search)
                | Q(worker_engagement__worker__email__icontains=search)
                | Q(
                    worker_engagement__work_order__work_order_number__icontains=search
                )
                | Q(
                    worker_engagement__engagement__engagement_number__icontains=search
                )
                | Q(
                    worker_engagement__engagement__work_order__work_order_number__icontains=search
                )
            )

        page, page_size = _pagination_params(
            request,
            default_page_size=self.DEFAULT_PAGE_SIZE,
            max_page_size=self.MAX_PAGE_SIZE,
        )
        page_obj, paginator = _paginate(queryset, page, page_size)
        records = list(page_obj.object_list) if page_obj else []
        results = [lifecycle_summary(run) for run in records]
        return Response(
            {
                "results": results,
                "summary": {
                    "active_gate_blockers": sum(
                        1
                        for item in results
                        if item["active_gate_blocker"]
                    ),
                    "total": paginator.count,
                },
                "pagination": _pagination_payload(
                    page_obj=page_obj,
                    paginator=paginator,
                    page_size=page_size,
                ),
            },
            status=status.HTTP_200_OK,
        )


class WorkerLifecycleDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def get(self, request, worker_id, lifecycle_type):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        worker = get_object_or_404(
            Worker.objects.filter(tenant_id=request.tenant.id),
            pk=worker_id,
        )
        if not can_access_worker(request, worker):
            raise PermissionDenied()
        run = _get_worker_run(
            request=request,
            worker=worker,
            lifecycle_type=lifecycle_type,
        )
        if run is None:
            return Response(
                {
                    "detail": (
                        f"No {lifecycle_type} lifecycle run exists for this worker."
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            lifecycle_detail(run, request=request),
            status=status.HTTP_200_OK,
        )


class LifecycleActivityUpdateView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, worker_id, lifecycle_type, activity_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        activity = get_object_or_404(
            LifecycleActivity.objects.select_related(
                "block__run__worker_engagement__worker",
                "block__run__worker_engagement__work_order",
                "block__run__worker_engagement__engagement__work_order",
            ),
            pk=activity_id,
            block__run__worker_engagement__worker_id=worker_id,
            block__run__lifecycle_type=lifecycle_type,
            block__run__tenant_id=request.tenant.id,
        )
        if not can_update_activity(request, activity):
            raise PermissionDenied()
        serializer = LifecycleActivityUpdateSerializer(
            data=request.data or {}
        )
        serializer.is_valid(raise_exception=True)
        if (
            serializer.validated_data["status"]
            == LifecycleActivity.STATUS_WAIVED
        ):
            membership = get_membership(request)
            if not membership or membership.role not in INTERNAL_EDIT_ROLES:
                raise PermissionDenied(
                    "Only an internal lifecycle manager can waive an activity."
                )
        try:
            LifecycleService.update_activity(
                activity=activity,
                user=request.user,
                activity_status=serializer.validated_data["status"],
                evidence=serializer.validated_data.get("evidence"),
                notes=serializer.validated_data.get("notes"),
            )
        except LifecycleTransitionError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        run = _lifecycle_queryset(request).get(pk=activity.block.run_id)
        return Response(
            lifecycle_detail(run, request=request),
            status=status.HTTP_200_OK,
        )


class WorkerOffboardingStartView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, worker_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        if not can_manage_worker(request):
            raise PermissionDenied()
        worker = get_object_or_404(
            Worker.objects.filter(tenant_id=request.tenant.id),
            pk=worker_id,
        )
        engagement_id = _request_engagement_id(request)
        work_order_id = _request_work_order_id(request)
        assignment = (
            worker.engagements.select_related(
                "work_order__cost_center__business_unit",
                "work_order__site",
                "engagement__work_order__cost_center__business_unit",
                "engagement__work_order__site",
            )
            .filter(
                status__in=[
                    "onboarding",
                    "active",
                    "offboarding",
                ]
            )
            .filter(
                **(
                    {"engagement_id": engagement_id}
                    if engagement_id is not None
                    else {"work_order_id": work_order_id}
                    if work_order_id is not None
                    else {}
                )
            )
            .order_by("-created_at")
            .first()
        )
        if assignment is None:
            return Response(
                {"detail": "Worker has no active assignment to offboard."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            run, created = LifecycleService.start_offboarding(
                worker_engagement=assignment
            )
        except LifecycleConfigurationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        run = _lifecycle_queryset(request).get(pk=run.pk)
        return Response(
            lifecycle_detail(run, request=request),
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )


class WorkerInviteView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember]

    def post(self, request, worker_id):
        tenant_error = _ensure_tenant_context(request)
        if tenant_error:
            return tenant_error
        if not can_manage_worker(request):
            raise PermissionDenied()
        worker = get_object_or_404(
            Worker.objects.filter(tenant_id=request.tenant.id),
            pk=worker_id,
        )
        if worker.user_id:
            return Response(
                {"detail": "Worker is already registered."},
                status=status.HTTP_409_CONFLICT,
            )
        engagement_id = _request_engagement_id(request)
        work_order_id = _request_work_order_id(request)
        assignments = worker.engagements.select_related(
            "work_order",
            "engagement",
        )
        if engagement_id is not None:
            assignments = assignments.filter(engagement_id=engagement_id)
        elif work_order_id is not None:
            assignments = assignments.filter(work_order_id=work_order_id)
        assignment = assignments.order_by("-created_at").first()
        if assignment is None:
            return Response(
                {"detail": "Worker has no work order assignment."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            invite, link = WorkerInviteService.issue(
                worker=worker,
                work_order=assignment.resolved_work_order,
                engagement=(
                    assignment.engagement
                    if assignment.engagement_id
                    else None
                ),
                invited_by=request.user,
                base_url=request.build_absolute_uri("/").rstrip("/"),
            )
        except InviteDeliveryError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(
            {
                "invite_id": invite.id,
                "worker_id": worker.id,
                "email": invite.email,
                "expires_at": invite.expires_at,
                "registration_link": link,
            },
            status=status.HTTP_201_CREATED,
        )


class WorkerRegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response(
                {"detail": "Tenant context is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = WorkerRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"].strip().lower()

        with transaction.atomic():
            invite = (
                WorkerInvite.objects.select_for_update()
                .select_related("worker")
                .filter(token=data["worker_invite_token"])
                .first()
            )
            if not invite:
                return Response(
                    {"detail": "Invite is invalid."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if invite.is_expired():
                invite.mark_expired()
                return Response(
                    {"detail": "Invite has expired."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if invite.status != WorkerInvite.STATUS_PENDING:
                return Response(
                    {"detail": "Invite is no longer active."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if invite.email.strip().lower() != email:
                return Response(
                    {"detail": "Invite email does not match."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            worker = Worker.objects.select_for_update().get(
                pk=invite.worker_id
            )
            user = User.objects.filter(
                Q(email__iexact=email) | Q(username__iexact=email)
            ).first()
            if user and user.auth_type == User.AUTH_SSO:
                return Response(
                    {"detail": "SSO users cannot use password signup."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if worker.user_id and (
                not user or worker.user_id != user.id
            ):
                return Response(
                    {"detail": "Worker is already linked to another user."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            membership = (
                Membership.objects.filter(user=user, tenant=tenant).first()
                if user
                else None
            )
            if membership and membership.role != Membership.ROLE_WORKER:
                return Response(
                    {
                        "detail": (
                            "This email already has a non-worker account "
                            "in the tenant."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if user and not user.check_password(data["password"]):
                return Response(
                    {
                        "detail": (
                            "Existing user password is incorrect for this email."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not user:
                try:
                    validate_password_policy(data["password"], tenant)
                except DjangoValidationError as exc:
                    messages = list(getattr(exc, "messages", []) or [])
                    return Response(
                        {
                            "detail": messages
                            or [
                                "Password does not meet policy requirements."
                            ]
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            linked_existing_user = user is not None
            if not user:
                first_name, last_name = _split_worker_name(worker.full_name)
                user = User(
                    username=email,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    auth_type=User.AUTH_PASSWORD,
                    is_active=True,
                )
                user.set_password(data["password"])
                user.save()
                record_password_history(user, tenant)

            if membership and (
                membership.status != Membership.STATUS_ACTIVE
                or not membership.is_active
            ):
                membership.status = Membership.STATUS_ACTIVE
                membership.is_active = True
                membership.save(
                    update_fields=["status", "is_active"]
                )
            elif not membership:
                membership = Membership(
                    user=user,
                    tenant=tenant,
                    role=Membership.ROLE_WORKER,
                    status=Membership.STATUS_ACTIVE,
                    is_active=True,
                )
                membership.full_clean()
                membership.save()

            worker.user = user
            worker.registered_at = timezone.now()
            worker.status = Worker.STATUS_ONBOARDING
            worker.save(
                update_fields=[
                    "user",
                    "registered_at",
                    "status",
                    "updated_at",
                ]
            )
            invite.mark_accepted(user=user)

        next_path = (
            f"/workers/{worker.id}/engagements/onboarding/workspace"
        )
        if invite.work_order_id:
            next_path = f"{next_path}?work_order={invite.work_order_id}"
        elif invite.engagement_id:
            next_path = f"{next_path}?engagement={invite.engagement_id}"
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "worker_id": worker.id,
                "linked_existing_user": linked_existing_user,
                "next": next_path,
            },
            status=status.HTTP_201_CREATED,
        )


def _engagement_queryset(request):
    return (
        Engagement.objects.filter(tenant_id=request.tenant.id)
        .select_related(
            "work_order__intake",
            "work_order__supplier",
            "work_order__role_definition",
            "work_order__cost_center__business_unit",
            "work_order__site",
            "created_by",
            "accepted_by",
            "change_requested_by",
        )
        .prefetch_related(
            "worker_engagement__worker__invites",
            "worker_engagement__lifecycle_runs__blocks__activities",
        )
    )


def _worker_directory_queryset(request):
    membership = get_membership(request)
    assignments = (
        WorkerEngagement.objects.select_related(
            "work_order__intake",
            "work_order__supplier",
            "work_order__role_definition",
            "work_order__cost_center__business_unit",
            "work_order__site",
            "work_order__created_by",
            "engagement",
            "engagement__work_order__intake",
            "engagement__work_order__supplier",
            "engagement__work_order__role_definition",
            "engagement__work_order__cost_center__business_unit",
            "engagement__work_order__site",
            "engagement__work_order__created_by",
        )
        .prefetch_related(
            Prefetch(
                "lifecycle_runs",
                queryset=LifecycleRun.objects.order_by(
                    "lifecycle_type",
                    "-created_at",
                ).prefetch_related(
                    Prefetch(
                        "blocks",
                        queryset=LifecycleBlock.objects.order_by(
                            "sequence",
                            "id",
                        ).prefetch_related("activities"),
                    )
                ),
            )
        )
        .order_by("-created_at", "-id")
    )
    queryset = Worker.objects.filter(
        tenant_id=request.tenant.id
    ).select_related("user")

    if membership and membership.role == Membership.ROLE_SUPPLIER:
        assignments = assignments.filter(
            Q(work_order__supplier_id=membership.supplier_id)
            | Q(engagement__work_order__supplier_id=membership.supplier_id)
        )
        queryset = queryset.filter(
            Q(engagements__work_order__supplier_id=membership.supplier_id)
            | Q(
                engagements__engagement__work_order__supplier_id=(
                    membership.supplier_id
                )
            )
        ).distinct()
    elif membership and membership.role == Membership.ROLE_WORKER:
        assignments = assignments.filter(worker__user=request.user)
        queryset = queryset.filter(user=request.user)

    return queryset.prefetch_related(
        Prefetch(
            "engagements",
            queryset=assignments,
            to_attr="visible_assignments",
        )
    )


def _lifecycle_queryset(request):
    return (
        LifecycleRun.objects.filter(tenant_id=request.tenant.id)
        .select_related(
            "workflow",
            "worker_engagement__worker",
            "worker_engagement__work_order__intake",
            "worker_engagement__work_order__supplier",
            "worker_engagement__work_order__role_definition",
            "worker_engagement__work_order__cost_center__business_unit",
            "worker_engagement__work_order__site",
            "worker_engagement__work_order__created_by",
            "worker_engagement__engagement__work_order__intake",
            "worker_engagement__engagement__work_order__supplier",
            "worker_engagement__engagement__work_order__role_definition",
            "worker_engagement__engagement__work_order__cost_center__business_unit",
            "worker_engagement__engagement__work_order__site",
            "worker_engagement__engagement__work_order__created_by",
        )
        .prefetch_related(
            Prefetch(
                "blocks",
                queryset=LifecycleBlock.objects.order_by(
                    "sequence",
                    "id",
                ).prefetch_related("activities"),
            ),
            "worker_engagement__worker__invites",
        )
        .order_by(
            "worker_engagement__work_order__start_date",
            "worker_engagement__worker__full_name",
            "id",
        )
    )


def _get_worker_run(*, request, worker, lifecycle_type):
    if lifecycle_type not in {
        LifecycleRun.TYPE_ONBOARDING,
        LifecycleRun.TYPE_OFFBOARDING,
    }:
        return None
    engagement_id = (request.GET.get("engagement") or "").strip()
    work_order_id = (request.GET.get("work_order") or "").strip()
    queryset = _lifecycle_queryset(request).filter(
        worker_engagement__worker=worker,
        lifecycle_type=lifecycle_type,
    )
    if engagement_id.isdigit():
        queryset = queryset.filter(
            worker_engagement__engagement_id=engagement_id
        )
    elif work_order_id.isdigit():
        queryset = queryset.filter(
            worker_engagement__work_order_id=work_order_id
        )
    return queryset.order_by("-created_at", "-id").first()


def _request_engagement_id(request):
    raw_value = request.data.get("engagement_id")
    if raw_value in (None, ""):
        raw_value = request.GET.get("engagement")
    if raw_value in (None, ""):
        return None
    try:
        engagement_id = int(raw_value)
    except (TypeError, ValueError):
        raise ValidationError("Invalid engagement selection.")
    if engagement_id <= 0:
        raise ValidationError("Invalid engagement selection.")
    return engagement_id


def _request_work_order_id(request):
    raw_value = request.data.get("work_order_id")
    if raw_value in (None, ""):
        raw_value = request.GET.get("work_order")
    if raw_value in (None, ""):
        return None
    try:
        work_order_id = int(raw_value)
    except (TypeError, ValueError):
        raise ValidationError("Invalid work order selection.")
    if work_order_id <= 0:
        raise ValidationError("Invalid work order selection.")
    return work_order_id


def _assert_engagement_access(request, engagement):
    membership = get_membership(request)
    if not membership:
        raise PermissionDenied()
    if membership.role == Membership.ROLE_SUPPLIER:
        if membership.supplier_id != engagement.work_order.supplier_id:
            raise PermissionDenied()
    elif membership.role == Membership.ROLE_WORKER:
        worker = getattr(
            getattr(engagement, "worker_engagement", None),
            "worker",
            None,
        )
        if not worker or worker.user_id != request.user.id:
            raise PermissionDenied()


def _assert_supplier_action_access(request, engagement):
    membership = get_membership(request)
    if not membership:
        raise PermissionDenied()
    if membership.role == Membership.ROLE_SUPPLIER:
        if membership.supplier_id != engagement.work_order.supplier_id:
            raise PermissionDenied()
        return
    if membership.role not in INTERNAL_EDIT_ROLES:
        raise PermissionDenied()


def _ensure_tenant_context(request):
    tenant = getattr(request, "tenant", None)
    if not tenant or tenant.schema_name == "public":
        return Response(
            {"detail": "Tenant context is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _parse_positive_int(value, *, default, field_name):
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    if parsed < 1:
        raise LifecycleTransitionError(
            f"{field_name} must be a positive integer."
        )
    return parsed


def _pagination_params(
    request,
    *,
    default_page_size,
    max_page_size,
):
    try:
        page = _parse_positive_int(
            request.GET.get("page"),
            default=1,
            field_name="page",
        )
        page_size = _parse_positive_int(
            request.GET.get("page_size"),
            default=default_page_size,
            field_name="page_size",
        )
    except LifecycleTransitionError:
        page = 1
        page_size = default_page_size
    return page, min(page_size, max_page_size)


def _paginate(queryset, page, page_size):
    paginator = Paginator(queryset, page_size)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else None
    return page_obj, paginator


def _pagination_payload(*, page_obj, paginator, page_size):
    return {
        "page": page_obj.number if page_obj else 1,
        "page_size": page_size,
        "total_count": paginator.count,
        "total_pages": paginator.num_pages,
        "has_next": bool(page_obj and page_obj.has_next()),
        "has_previous": bool(page_obj and page_obj.has_previous()),
    }


def _split_worker_name(full_name):
    parts = [part for part in (full_name or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])
