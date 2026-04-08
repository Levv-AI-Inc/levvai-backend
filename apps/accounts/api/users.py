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

from apps.accounts.models import Membership, User
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
        if user:
            membership = Membership.objects.filter(user=user, tenant=tenant).first()
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
        if not membership:
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
        bind_session_to_tenant(request, tenant)

        return Response({"detail": "ok"}, status=status.HTTP_200_OK)


class AdminUserListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantMember, HasRole]
    required_roles = [Membership.ROLE_ADMIN]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        queryset = Membership.objects.filter(tenant=tenant).select_related("user").order_by(
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
