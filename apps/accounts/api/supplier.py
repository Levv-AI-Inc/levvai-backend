from django.contrib.auth import authenticate, login
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership, SupplierInvite, User
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

        email = data["email"].strip().lower()
        supplier_id = data.get("supplier_id")
        invite = None

        invite_token = data.get("invite_token")
        if invite_token:
            invite = SupplierInvite.objects.filter(token=invite_token).first()
            if not invite:
                return Response({"detail": "Invite is invalid."}, status=status.HTTP_400_BAD_REQUEST)
            if invite.tenant_id != tenant.id:
                return Response({"detail": "Invite does not belong to this tenant."}, status=status.HTTP_400_BAD_REQUEST)
            if invite.is_expired():
                invite.mark_expired()
                return Response({"detail": "Invite has expired."}, status=status.HTTP_400_BAD_REQUEST)
            if invite.status != SupplierInvite.STATUS_PENDING:
                return Response({"detail": "Invite is no longer active."}, status=status.HTTP_400_BAD_REQUEST)
            if invite.email.strip().lower() != email:
                return Response({"detail": "Invite email does not match."}, status=status.HTTP_400_BAD_REQUEST)
            supplier_id = invite.supplier_id

        supplier = Supplier.objects.filter(id=supplier_id).first()
        if not supplier:
            return Response({"detail": "Supplier not found."}, status=status.HTTP_400_BAD_REQUEST)

        existing_user = User.objects.filter(email__iexact=email).first() or User.objects.filter(
            username__iexact=email
        ).first()

        with transaction.atomic():
            linked_existing_user = False
            if existing_user:
                linked_existing_user = True
                user = existing_user
                if not user.check_password(data["password"]):
                    return Response(
                        {"detail": "Existing user password is incorrect for this email."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                try:
                    validate_password_policy(data["password"], tenant)
                except ValidationError as exc:
                    messages = list(getattr(exc, "messages", []) or [])
                    if not messages:
                        messages = ["Password does not meet policy requirements."]
                    return Response({"detail": messages}, status=status.HTTP_400_BAD_REQUEST)
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
                record_password_history(user, tenant)

            membership = Membership.objects.filter(user=user, tenant=tenant).first()
            if membership and membership.role != Membership.ROLE_SUPPLIER:
                return Response({"detail": "User already exists in this tenant as a non-supplier."}, status=status.HTTP_400_BAD_REQUEST)

            if membership:
                membership.status = Membership.STATUS_ACTIVE
                membership.is_active = True
                membership.supplier_id = supplier.id
                membership.full_clean()
                membership.save()
            else:
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

            if invite and invite.status == SupplierInvite.STATUS_PENDING:
                invite.mark_accepted(user=user)
                if supplier.status != Supplier.STATUS_ACTIVE:
                    supplier.status = Supplier.STATUS_ACTIVE
                    supplier.save(update_fields=["status"])

        return Response(
            {
                "id": user.id,
                "email": user.email,
                "supplier_id": supplier.id,
                "linked_existing_user": linked_existing_user,
            },
            status=status.HTTP_201_CREATED,
        )


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
