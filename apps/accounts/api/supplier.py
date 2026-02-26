from django.contrib.auth import authenticate, login
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership, User
from apps.accounts.password_policy import (
    get_password_policy,
    password_is_expired,
    record_password_history,
    register_failed_login,
    register_successful_login,
    validate_password_policy,
)
from apps.accounts.session_scope import bind_session_to_tenant
from apps.accounts.serializers import SupplierLoginSerializer, SupplierRegisterSerializer
from apps.masterdata.models import Supplier


class SupplierRegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = SupplierRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        supplier = Supplier.objects.filter(id=data["supplier_id"]).first()
        if not supplier:
            return Response({"detail": "Supplier not found."}, status=status.HTTP_400_BAD_REQUEST)

        email = data["email"].strip().lower()
        existing_user = User.objects.filter(email__iexact=email).first() or User.objects.filter(
            username__iexact=email
        ).first()
        if existing_user:
            return Response({"detail": "User already exists."}, status=status.HTTP_400_BAD_REQUEST)

        validate_password_policy(data["password"], tenant)

        with transaction.atomic():
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

            membership = Membership(
                user=user,
                tenant=tenant,
                role=Membership.ROLE_SUPPLIER,
                status=Membership.STATUS_ACTIVE,
                is_active=True,
                supplier_id=supplier.id,
            )
            membership.full_clean()
            membership.save()

            record_password_history(user, tenant)

        return Response({"id": user.id, "email": user.email}, status=status.HTTP_201_CREATED)


class SupplierPasswordLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = SupplierLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first() or User.objects.filter(
            username__iexact=email
        ).first()
        if not user:
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        membership = Membership.objects.filter(
            user=user,
            tenant=tenant,
            role=Membership.ROLE_SUPPLIER,
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
