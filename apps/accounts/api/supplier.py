from django.contrib.auth import authenticate, login
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Membership, SupplierContactAccess, User, WorkerProfile
from apps.accounts.profile import (
    WORKER_HOME_PATH,
    build_membership_metadata,
    build_worker_profile_metadata,
    resolve_frontend_path_for_membership,
)
from apps.accounts.worker_accounts import WorkerInviteValidationError, register_worker_invite
from apps.accounts.password_policy import (
    get_password_policy,
    password_is_expired,
    register_failed_login,
    register_successful_login,
)
from apps.accounts.session_scope import bind_session_to_tenant
from apps.accounts.supplier_contact_access import (
    SupplierContactAccessError,
    accept_contact_invitation,
)
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
        invite_token = data.get("invite_token")
        if invite_token and invite_token.startswith("worker_"):
            try:
                user, worker_profile, engagement = register_worker_invite(
                    tenant=tenant,
                    email=email,
                    password=data["password"],
                    token=invite_token,
                )
            except WorkerInviteValidationError as exc:
                detail = exc.args[0] if exc.args else str(exc)
                return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

            return Response(
                {
                    "id": user.id,
                    "email": user.email,
                    "worker_profile_id": worker_profile.id,
                    "worker_engagement_id": engagement.id,
                    "profile": build_worker_profile_metadata(),
                    "linked_existing_user": False,
                    "next": WORKER_HOME_PATH,
                },
                status=status.HTTP_201_CREATED,
            )

        if invite_token:
            try:
                access, membership, linked_existing_user = accept_contact_invitation(
                    tenant=tenant,
                    token=invite_token,
                    email=email,
                    password=data["password"],
                    first_name=data.get("first_name", ""),
                    last_name=data.get("last_name", ""),
                )
            except SupplierContactAccessError as exc:
                detail = exc.args[0] if exc.args else str(exc)
                return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

            supplier = Supplier.objects.filter(id=access.supplier_id).first()
            if not supplier:
                return Response({"detail": "Supplier not found."}, status=status.HTTP_400_BAD_REQUEST)
            if supplier.status != Supplier.STATUS_ACTIVE:
                supplier.status = Supplier.STATUS_ACTIVE
                supplier.save(update_fields=["status"])
            return Response(
                {
                    "id": membership.user_id,
                    "email": membership.user.email,
                    "supplier_id": access.supplier_id,
                    "linked_existing_user": linked_existing_user,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {"detail": "A valid supplier invitation is required."},
            status=status.HTTP_400_BAD_REQUEST,
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
        has_reviewed_access = SupplierContactAccess.objects.filter(
            membership=membership,
            tenant=tenant,
            supplier_id=membership.supplier_id,
            email__iexact=email,
            status=SupplierContactAccess.STATUS_ACTIVE,
        ).exists()
        if not has_reviewed_access:
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
        bind_session_to_tenant(request, tenant, membership)

        membership_metadata = build_membership_metadata(membership)
        redirect_to = resolve_frontend_path_for_membership(
            membership,
            data.get("next"),
        )

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
