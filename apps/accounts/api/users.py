from django.conf import settings
from django.contrib.auth import authenticate, login
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership, User, WorkerProfile
from apps.accounts.profile import (
    PROFILE_WORKER,
    active_worker_engagements_for_profile,
    build_membership_metadata,
    build_worker_profile_metadata,
    build_worker_session_metadata,
    get_active_worker_profile,
    resolve_frontend_path_for_membership,
    resolve_frontend_path_for_profile,
)
from apps.common.permissions import HasRole, IsTenantMember
from apps.accounts.password_policy import (
    get_password_policy,
    password_is_expired,
    record_password_history,
    register_failed_login,
    register_successful_login,
    validate_password_policy,
)
from apps.accounts.session_scope import bind_session_to_tenant
from apps.accounts.serializers import UserLoginSerializer, UserRegisterSerializer


class UserRegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = UserRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
        if user and user.auth_type == User.AUTH_SSO:
            return Response({"detail": "SSO users cannot use password signup."}, status=status.HTTP_400_BAD_REQUEST)

        membership = None
        worker_profile = None
        if user:
            worker_profile = WorkerProfile.objects.filter(user=user).first()
            if worker_profile and worker_profile.status != WorkerProfile.STATUS_ACTIVE:
                return Response({"detail": "Worker account is disabled."}, status=status.HTTP_403_FORBIDDEN)
            membership = Membership.objects.filter(user=user, tenant=tenant).first()
            if worker_profile and not membership:
                return Response(
                    {"detail": "Worker registration requires a valid invitation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if membership and membership.role == Membership.ROLE_SUPPLIER:
                return Response({"detail": "Supplier users cannot use this signup."}, status=status.HTTP_400_BAD_REQUEST)
            if membership and membership.status == Membership.STATUS_ACTIVE and membership.is_active:
                return Response({"detail": "User already exists in this tenant."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_password_policy(data["password"], tenant, user=user)
        except ValidationError as exc:
            messages = list(getattr(exc, "messages", []) or [])
            if not messages:
                messages = ["Password does not meet policy requirements."]
            return Response({"detail": messages}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            if not user:
                user = User(
                    username=email,
                    email=email,
                    first_name=data.get("first_name", ""),
                    last_name=data.get("last_name", ""),
                    auth_type=User.AUTH_PASSWORD,
                    is_active=True,
                )
                user.set_password(data["password"])
                user.save()
            else:
                if data.get("first_name") and user.first_name != data.get("first_name"):
                    user.first_name = data.get("first_name")
                if data.get("last_name") and user.last_name != data.get("last_name"):
                    user.last_name = data.get("last_name")
                user.set_password(data["password"])
                user.auth_type = User.AUTH_PASSWORD
                user.save()

            role = settings.PASSWORD_DEFAULT_ROLE
            valid_roles = {choice[0] for choice in Membership.ROLE_CHOICES}
            if role not in valid_roles or role == Membership.ROLE_SUPPLIER:
                role = Membership.ROLE_BUSINESS

            if membership:
                membership.role = role
                membership.status = Membership.STATUS_ACTIVE
                membership.is_active = True
                membership.supplier_id = None
                membership.full_clean()
                membership.save()
            else:
                membership = Membership(
                    user=user,
                    tenant=tenant,
                    role=role,
                    status=Membership.STATUS_ACTIVE,
                    is_active=True,
                )
                membership.full_clean()
                membership.save()

            record_password_history(user, tenant)

        return Response({"id": user.id, "email": user.email}, status=status.HTTP_201_CREATED)


class UserPasswordLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = UserLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
        if not user:
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)
        if user.auth_type == User.AUTH_SSO:
            return Response({"detail": "SSO users cannot use password login."}, status=status.HTTP_400_BAD_REQUEST)

        membership = Membership.objects.filter(
            user=user,
            tenant=tenant,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        ).first()
        worker_profile = get_active_worker_profile(user)
        if not membership:
            if not worker_profile or not active_worker_engagements_for_profile(worker_profile).exists():
                return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        policy = get_password_policy()
        attempt = user.loginattempt_set.filter(tenant=tenant).first()
        if attempt and attempt.locked_until and attempt.locked_until > timezone.now():
            return Response({"detail": "Account locked. Try again later."}, status=status.HTTP_403_FORBIDDEN)

        authenticated = authenticate(request, username=user.username, password=data["password"])
        if not authenticated:
            register_failed_login(user, tenant, policy)
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        if password_is_expired(user, tenant, policy):
            return Response({"detail": "Password expired."}, status=status.HTTP_403_FORBIDDEN)

        register_successful_login(user, tenant)
        login(request, authenticated)

        if membership:
            bind_session_to_tenant(request, tenant)
            membership_metadata = build_membership_metadata(membership)
            redirect_to = resolve_frontend_path_for_membership(membership, data.get("next"))
            return Response(
                {
                    "detail": "ok",
                    "profile": {
                        "type": membership_metadata["profile_type"],
                        **membership_metadata,
                    },
                    "membership": membership_metadata,
                    "default_home": membership_metadata["default_home"],
                    "redirect_to": redirect_to,
                },
                status=status.HTTP_200_OK,
            )

        worker = build_worker_session_metadata(request, worker_profile)
        profile_metadata = build_worker_profile_metadata()
        redirect_to = resolve_frontend_path_for_profile(PROFILE_WORKER, data.get("next"))
        return Response(
            {
                "detail": "ok",
                "profile": profile_metadata,
                "worker": worker,
                "default_home": profile_metadata["default_home"],
                "redirect_to": redirect_to,
            },
            status=status.HTTP_200_OK,
        )


class AdminUserListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        queryset = Membership.objects.filter(
            tenant=tenant,
            user__worker_profile__isnull=True,
        ).exclude(role=Membership.ROLE_SUPPLIER).select_related("user").order_by(
            "user__first_name",
            "user__last_name",
            "user__email",
        )

        role_param = (request.GET.get("role") or "").strip().lower()
        if role_param:
            queryset = queryset.filter(role=role_param)

        status_param = (request.GET.get("status") or "").strip().lower()
        if status_param:
            queryset = queryset.filter(status=status_param)

        business_unit_id_param = (request.GET.get("business_unit_id") or "").strip()
        if business_unit_id_param.isdigit():
            queryset = queryset.filter(business_unit_id=int(business_unit_id_param))

        cost_center_id_param = (request.GET.get("cost_center_id") or "").strip()
        if cost_center_id_param.isdigit():
            queryset = queryset.filter(cost_center_id=int(cost_center_id_param))

        search_term = (request.GET.get("search") or request.GET.get("q") or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(user__first_name__icontains=search_term)
                | Q(user__last_name__icontains=search_term)
                | Q(user__email__icontains=search_term)
                | Q(user__username__icontains=search_term)
                | Q(role__icontains=search_term)
            )

        memberships = list(queryset[:200])
        business_unit_map = {}
        cost_center_map = {}

        business_unit_ids = {m.business_unit_id for m in memberships if m.business_unit_id}
        cost_center_ids = {m.cost_center_id for m in memberships if m.cost_center_id}
        if business_unit_ids or cost_center_ids:
            from apps.masterdata.models import BusinessUnit, CostCenter

            if business_unit_ids:
                business_unit_map = {
                    item.id: item.name for item in BusinessUnit.objects.filter(id__in=business_unit_ids)
                }
            if cost_center_ids:
                cost_center_map = {
                    item.id: {"name": item.name, "code": item.code}
                    for item in CostCenter.objects.filter(id__in=cost_center_ids)
                }

        results = []
        for membership in memberships:
            user = membership.user
            full_name = (user.get_full_name() or "").strip()
            cost_center = cost_center_map.get(membership.cost_center_id)
            results.append(
                {
                    "membership_id": membership.id,
                    "user_id": user.id,
                    "name": full_name or user.username or user.email,
                    "email": user.email,
                    "status": membership.status,
                    "role": membership.role,
                    "business_unit_id": membership.business_unit_id,
                    "business_unit": business_unit_map.get(membership.business_unit_id),
                    "cost_center_id": membership.cost_center_id,
                    "cost_center": cost_center.get("code") if cost_center else None,
                    "cost_center_name": cost_center.get("name") if cost_center else None,
                    "sso_enabled": user.auth_type == User.AUTH_SSO,
                    "is_active": membership.is_active,
                }
            )

        return Response({"results": results}, status=status.HTTP_200_OK)

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        email = (request.data.get("email") or "").strip().lower()
        name = (request.data.get("name") or "").strip()
        role = (request.data.get("role") or Membership.ROLE_READ_ONLY).strip().lower()
        membership_status = (request.data.get("status") or Membership.STATUS_INVITED).strip().lower()

        valid_roles = {choice[0] for choice in Membership.ROLE_CHOICES} - {Membership.ROLE_SUPPLIER}
        valid_statuses = {choice[0] for choice in Membership.STATUS_CHOICES}
        if not email or "@" not in email:
            return Response({"detail": "A valid email address is required."}, status=status.HTTP_400_BAD_REQUEST)
        if role not in valid_roles:
            return Response({"detail": "Select a valid internal access role."}, status=status.HTTP_400_BAD_REQUEST)
        if membership_status not in valid_statuses:
            return Response({"detail": "Select a valid user status."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).first()
        if user and WorkerProfile.objects.filter(user=user).exists():
            return Response(
                {"detail": "Worker accounts are managed in the Workers directory."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if user and Membership.objects.filter(user=user, tenant=tenant).exists():
            return Response({"detail": "This user already belongs to the tenant."}, status=status.HTTP_400_BAD_REQUEST)

        name_parts = name.split(None, 1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        try:
            with transaction.atomic():
                if not user:
                    user = User.objects.create(
                        username=email,
                        email=email,
                        first_name=first_name,
                        last_name=last_name,
                        auth_type=User.AUTH_SSO if request.data.get("sso_enabled") else User.AUTH_PASSWORD,
                        is_active=membership_status != Membership.STATUS_DISABLED,
                    )
                    user.set_unusable_password()
                    user.save(update_fields=["password"])
                else:
                    user.email = email
                    user.first_name = first_name
                    user.last_name = last_name
                    user.auth_type = User.AUTH_SSO if request.data.get("sso_enabled") else User.AUTH_PASSWORD
                    user.is_active = membership_status != Membership.STATUS_DISABLED
                    user.save(update_fields=["email", "first_name", "last_name", "auth_type", "is_active"])

                membership = Membership(
                    user=user,
                    tenant=tenant,
                    role=role,
                    status=membership_status,
                    is_active=membership_status != Membership.STATUS_DISABLED,
                    business_unit_id=request.data.get("business_unit_id") or None,
                    cost_center_id=request.data.get("cost_center_id") or None,
                )
                membership.full_clean()
                membership.save()
        except ValidationError as exc:
            return Response(
                getattr(exc, "message_dict", {"detail": exc.messages}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(_admin_membership_payload(membership), status=status.HTTP_201_CREATED)


class AdminUserDetailView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN]

    def patch(self, request, membership_id):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        membership = Membership.objects.filter(
            id=membership_id,
            tenant=tenant,
            user__worker_profile__isnull=True,
        ).exclude(role=Membership.ROLE_SUPPLIER).select_related("user").first()
        if not membership:
            return Response({"detail": "User membership was not found."}, status=status.HTTP_404_NOT_FOUND)

        role = (request.data.get("role", membership.role) or "").strip().lower()
        membership_status = (request.data.get("status", membership.status) or "").strip().lower()
        valid_roles = {choice[0] for choice in Membership.ROLE_CHOICES} - {Membership.ROLE_SUPPLIER}
        valid_statuses = {choice[0] for choice in Membership.STATUS_CHOICES}
        if role not in valid_roles:
            return Response({"detail": "Select a valid internal access role."}, status=status.HTTP_400_BAD_REQUEST)
        if membership_status not in valid_statuses:
            return Response({"detail": "Select a valid user status."}, status=status.HTTP_400_BAD_REQUEST)

        user = membership.user
        name = (request.data.get("name", user.get_full_name()) or "").strip()
        email = (request.data.get("email", user.email) or "").strip().lower()
        if not email or "@" not in email:
            return Response({"detail": "A valid email address is required."}, status=status.HTTP_400_BAD_REQUEST)
        duplicate = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).exclude(id=user.id).exists()
        if duplicate:
            return Response({"detail": "Another user already uses this email address."}, status=status.HTTP_400_BAD_REQUEST)

        name_parts = name.split(None, 1)
        try:
            with transaction.atomic():
                user.email = email
                user.username = email
                user.first_name = name_parts[0] if name_parts else ""
                user.last_name = name_parts[1] if len(name_parts) > 1 else ""
                if "sso_enabled" in request.data:
                    user.auth_type = User.AUTH_SSO if request.data.get("sso_enabled") else User.AUTH_PASSWORD
                user.is_active = membership_status != Membership.STATUS_DISABLED
                user.save()

                membership.role = role
                membership.status = membership_status
                membership.is_active = membership_status != Membership.STATUS_DISABLED
                if "business_unit_id" in request.data:
                    membership.business_unit_id = request.data.get("business_unit_id") or None
                if "cost_center_id" in request.data:
                    membership.cost_center_id = request.data.get("cost_center_id") or None
                membership.full_clean()
                membership.save()
        except ValidationError as exc:
            return Response(
                getattr(exc, "message_dict", {"detail": exc.messages}),
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(_admin_membership_payload(membership), status=status.HTTP_200_OK)


def _admin_membership_payload(membership):
    from apps.masterdata.models import BusinessUnit, CostCenter

    user = membership.user
    business_unit = BusinessUnit.objects.filter(id=membership.business_unit_id).first() if membership.business_unit_id else None
    cost_center = CostCenter.objects.filter(id=membership.cost_center_id).first() if membership.cost_center_id else None
    return {
        "membership_id": membership.id,
        "user_id": user.id,
        "name": (user.get_full_name() or "").strip() or user.username or user.email,
        "email": user.email,
        "status": membership.status,
        "role": membership.role,
        "business_unit_id": membership.business_unit_id,
        "business_unit": business_unit.name if business_unit else None,
        "cost_center_id": membership.cost_center_id,
        "cost_center": cost_center.code if cost_center else None,
        "cost_center_name": cost_center.name if cost_center else None,
        "sso_enabled": user.auth_type == User.AUTH_SSO,
        "is_active": membership.is_active,
    }
