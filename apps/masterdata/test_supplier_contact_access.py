from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import Membership, SupplierContactAccess, SupplierInvite
from apps.accounts.supplier_contact_access import SupplierContactAccessError
from apps.masterdata.views import SupplierViewSet


def access_record(*, state=SupplierContactAccess.STATUS_PENDING, delivery=SupplierInvite.DELIVERY_SENT):
    invitation = SimpleNamespace(
        delivery_status=delivery,
        delivery_error="" if delivery != SupplierInvite.DELIVERY_FAILED else "Invitation email could not be sent.",
        expires_at=timezone.now(),
        sent_at=timezone.now(),
        is_expired=lambda: False,
    )
    invitations = MagicMock()
    invitations.order_by.return_value.first.return_value = invitation
    return SimpleNamespace(
        id=41,
        supplier_id=7,
        email="contact@supplier.test",
        status=state,
        membership_id=31,
        accepted_at=None,
        revoked_at=None,
        invitations=invitations,
    )


class SupplierContactAccessEndpointTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(id=5, is_authenticated=True)
        self.tenant = SimpleNamespace(id=11, name="Acme", schema_name="acme")
        self.supplier = SimpleNamespace(id=7)

    def request(self, method="get", path="/api/suppliers/7/contact-access/", data=None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return request

    def membership(self, role):
        return patch(
            "apps.common.permissions.get_active_tenant_membership",
            return_value=SimpleNamespace(role=role),
        )

    def test_list_is_scoped_by_url_supplier_and_tenant(self):
        record = access_record()
        queryset = MagicMock()
        queryset.prefetch_related.return_value = [record]
        view = SupplierViewSet.as_view({"get": "contact_access"})
        with (
            self.membership(Membership.ROLE_ADMIN),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch("apps.masterdata.views.SupplierContactAccess.objects.filter", return_value=queryset) as records,
        ):
            response = view(self.request(), pk=7)
        self.assertEqual(status.HTTP_200_OK, response.status_code)
        records.assert_called_once_with(tenant=self.tenant, supplier_id=7)
        self.assertEqual("contact@supplier.test", response.data["results"][0]["email"])

    def test_non_administrator_and_supplier_callers_are_denied_before_lookup(self):
        for role in (Membership.ROLE_MANAGER, Membership.ROLE_SUPPLIER):
            with self.subTest(role=role):
                view = SupplierViewSet.as_view({"get": "contact_access"})
                with self.membership(role), patch.object(SupplierViewSet, "get_object") as get_object:
                    response = view(self.request(), pk=7)
                self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)
                get_object.assert_not_called()

    def test_invite_response_never_exposes_token_or_registration_link(self):
        record = access_record(delivery=SupplierInvite.DELIVERY_FAILED)
        serializer = MagicMock(validated_data={"email": record.email, "expires_in_days": 7})
        view = SupplierViewSet.as_view({"post": "invite"})
        with (
            self.membership(Membership.ROLE_ADMIN),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch("apps.masterdata.views.SupplierInviteCreateSerializer", return_value=serializer),
            patch("apps.masterdata.views.create_contact_invitation", return_value=record),
        ):
            response = view(self.request("post", "/api/suppliers/7/invite/", {"email": record.email}), pk=7)
        self.assertEqual(status.HTTP_201_CREATED, response.status_code)
        self.assertNotIn("token", response.data)
        self.assertNotIn("registration_link", response.data)
        self.assertEqual(SupplierInvite.DELIVERY_FAILED, response.data["delivery_status"])

    def test_wrong_supplier_cannot_resend_or_revoke_access(self):
        view = SupplierViewSet.as_view({"post": "contact_access_resend"})
        with (
            self.membership(Membership.ROLE_ADMIN),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch(
                "apps.masterdata.views.resend_contact_invitation",
                side_effect=SupplierContactAccessError("not found"),
            ) as resend,
        ):
            response = view(self.request("post", "/api/suppliers/7/contact-access/41/resend/"), pk=7, access_id=41)
        self.assertEqual(status.HTTP_409_CONFLICT, response.status_code)
        self.assertEqual(7, resend.call_args.kwargs["supplier_id"])

    def test_revoke_returns_server_owned_lifecycle(self):
        record = access_record(state=SupplierContactAccess.STATUS_REVOKED)
        view = SupplierViewSet.as_view({"post": "contact_access_revoke"})
        with (
            self.membership(Membership.ROLE_ADMIN),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch("apps.masterdata.views.revoke_contact_access", return_value=record) as revoke,
        ):
            response = view(self.request("post", "/api/suppliers/7/contact-access/41/revoke/"), pk=7, access_id=41)
        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual(SupplierContactAccess.STATUS_REVOKED, response.data["status"])
        self.assertEqual([], response.data["allowed_actions"])
        self.assertEqual(7, revoke.call_args.kwargs["supplier_id"])
