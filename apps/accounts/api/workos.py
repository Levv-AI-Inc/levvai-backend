import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import login
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from workos import WorkOSClient

from apps.accounts.models import Membership, TenantSSOConfig, User


def _clean_next_url(next_url, fallback):
    if not next_url:
        return fallback
    if not isinstance(next_url, str):
        return fallback
    if next_url.startswith("//"):
        return fallback
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not next_url.startswith("/"):
        return fallback
    return next_url


class WorkOSLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        config = TenantSSOConfig.objects.filter(tenant=tenant, enabled=True).first()
        if not config:
            return Response({"detail": "SSO is not configured for this tenant."}, status=status.HTTP_400_BAD_REQUEST)

        if not settings.WORKOS_API_KEY or not settings.WORKOS_CLIENT_ID:
            return Response({"detail": "WorkOS is not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        state = secrets.token_urlsafe(24)
        request.session["workos_state"] = state
        next_url = _clean_next_url(request.GET.get("next"), settings.WORKOS_DEFAULT_NEXT_URL)
        request.session["workos_next"] = next_url

        redirect_uri = request.build_absolute_uri("/auth/workos/callback")
        workos = WorkOSClient(api_key=settings.WORKOS_API_KEY, client_id=settings.WORKOS_CLIENT_ID)

        params = {"redirect_uri": redirect_uri, "state": state}
        if config.workos_connection_id:
            params["connection_id"] = config.workos_connection_id
        else:
            params["organization_id"] = config.workos_organization_id

        authorization_url = workos.sso.get_authorization_url(**params)
        return redirect(authorization_url)


class WorkOSCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant or tenant.schema_name == "public":
            return Response({"detail": "Tenant context is required."}, status=status.HTTP_400_BAD_REQUEST)

        config = TenantSSOConfig.objects.filter(tenant=tenant, enabled=True).first()
        if not config:
            return Response({"detail": "SSO is not configured for this tenant."}, status=status.HTTP_400_BAD_REQUEST)

        if not settings.WORKOS_API_KEY or not settings.WORKOS_CLIENT_ID:
            return Response({"detail": "WorkOS is not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        code = request.GET.get("code")
        state = request.GET.get("state")
        expected_state = request.session.pop("workos_state", None)
        if not expected_state or state != expected_state:
            return Response({"detail": "Invalid SSO state."}, status=status.HTTP_400_BAD_REQUEST)
        if not code:
            return Response({"detail": "Missing authorization code."}, status=status.HTTP_400_BAD_REQUEST)

        workos = WorkOSClient(api_key=settings.WORKOS_API_KEY, client_id=settings.WORKOS_CLIENT_ID)
        profile_and_token = workos.sso.get_profile_and_token(code=code)
        profile = profile_and_token.profile

        org_id = getattr(profile, "organization_id", None) or getattr(profile, "organizationId", None)
        conn_id = getattr(profile, "connection_id", None) or getattr(profile, "connectionId", None)
        if config.workos_connection_id:
            if conn_id != config.workos_connection_id:
                return Response({"detail": "Invalid connection."}, status=status.HTTP_403_FORBIDDEN)
        else:
            if config.workos_organization_id and org_id != config.workos_organization_id:
                return Response({"detail": "Invalid organization."}, status=status.HTTP_403_FORBIDDEN)

        email = (getattr(profile, "email", None) or "").strip().lower()
        if not email:
            return Response({"detail": "Email is required from WorkOS."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=email).first()
        if not user:
            user = User.objects.create_user(
                username=email,
                email=email,
                first_name=getattr(profile, "first_name", "") or "",
                last_name=getattr(profile, "last_name", "") or "",
                auth_type=User.AUTH_SSO,
            )
        else:
            updates = []
            if getattr(user, "auth_type", None) != User.AUTH_SSO:
                user.auth_type = User.AUTH_SSO
                updates.append("auth_type")
            first_name = getattr(profile, "first_name", None)
            if first_name and user.first_name != first_name:
                user.first_name = first_name
                updates.append("first_name")
            last_name = getattr(profile, "last_name", None)
            if last_name and user.last_name != last_name:
                user.last_name = last_name
                updates.append("last_name")
            if updates:
                user.save(update_fields=updates)

        if not user.is_active:
            return Response({"detail": "User is disabled."}, status=status.HTTP_403_FORBIDDEN)

        if config.default_role == Membership.ROLE_SUPPLIER:
            role = settings.WORKOS_DEFAULT_ROLE
        else:
            role = config.default_role

        if role not in {choice[0] for choice in Membership.ROLE_CHOICES} or role == Membership.ROLE_SUPPLIER:
            role = Membership.ROLE_BUSINESS

        membership, created = Membership.objects.get_or_create(
            user=user,
            tenant=tenant,
            defaults={"role": role, "status": Membership.STATUS_ACTIVE, "is_active": True},
        )
        if not created:
            if membership.role == Membership.ROLE_SUPPLIER:
                return Response({"detail": "Supplier users cannot use SSO."}, status=status.HTTP_403_FORBIDDEN)
            if membership.status != Membership.STATUS_ACTIVE or not membership.is_active:
                return Response({"detail": "Membership is disabled."}, status=status.HTTP_403_FORBIDDEN)

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        next_url = request.session.pop("workos_next", None) or settings.WORKOS_DEFAULT_NEXT_URL
        next_url = _clean_next_url(next_url, settings.WORKOS_DEFAULT_NEXT_URL)
        return redirect(next_url)
