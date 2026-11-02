from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.accounts.api.supplier import SupplierPasswordLoginView, SupplierRegisterView
from apps.accounts.models import Membership, SupplierContactAccess, SupplierInvite, User
from apps.accounts.supplier_contact_access import (
    SupplierContactAccessError,
    _deliver,
    _validate_identity_target,
    accept_contact_invitation,
    resend_contact_invitation,
    revoke_contact_access,
    token_digest,
)


class SupplierContactAccessServiceTests(SimpleTestCase):
    def setUp(self):
        self.tenant = SimpleNamespace(id=11, name="Acme", schema_name="acme")
        self.user = MagicMock(id=21, email="contact@supplier.test", auth_type=User.AUTH_PASSWORD)
        self.membership = SimpleNamespace(
            id=31,
            user=self.user,
            user_id=21,
            tenant_id=11,
            role=Membership.ROLE_SUPPLIER,
            supplier_id=7,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
            authorization_version=4,
            full_clean=MagicMock(),
            save=MagicMock(),
        )
        self.access = SimpleNamespace(
            id=41,
            tenant=self.tenant,
            tenant_id=11,
            supplier_id=7,
            email="contact@supplier.test",
            membership=self.membership,
            membership_id=31,
            status=SupplierContactAccess.STATUS_ACTIVE,
            accepted_at=timezone.now(),
            revoked_at=None,
            save=MagicMock(),
        )

    def test_token_digest_does_not_store_raw_bearer_credential(self):
        digest = token_digest("supplier_secret")
        self.assertEqual(64, len(digest))
        self.assertNotEqual("supplier_secret", digest)

    def test_supplier_registration_cannot_authorize_from_supplier_id_and_email_alone(self):
        request = APIRequestFactory().post(
            "/auth/password/register",
            {
                "email": "contact@supplier.test",
                "password": "SafePassword!123",
                "supplier_id": 7,
            },
            format="json",
        )
        request.tenant = self.tenant
        response = SupplierRegisterView.as_view()(request)
        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertIn("invitation", response.data["detail"])

    def test_supplier_login_requires_matching_active_contact_access(self):
        request = APIRequestFactory().post(
            "/auth/password/login",
            {"email": self.access.email, "password": "ExistingPassword!123"},
            format="json",
        )
        request.tenant = self.tenant
        user_query = MagicMock()
        user_query.first.return_value = self.user
        membership_query = MagicMock()
        membership_query.first.return_value = self.membership
        access_query = MagicMock()
        access_query.exists.return_value = False
        with (
            patch.object(User.objects, "filter", return_value=user_query),
            patch.object(Membership.objects, "filter", return_value=membership_query),
            patch.object(SupplierContactAccess.objects, "filter", return_value=access_query),
        ):
            response = SupplierPasswordLoginView.as_view()(request)
        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertEqual("Invalid credentials.", response.data["detail"])

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SUPPLIER_INVITE_FROM_EMAIL="suppliers@levvai.test",
    )
    def test_delivery_uses_test_backend_and_records_sent_state(self):
        mail.outbox = []
        invite = MagicMock(expires_at=timezone.now() + timedelta(days=7))
        delivered = _deliver(
            invite=invite,
            access=self.access,
            raw_token="supplier_secret",
            base_url="https://acme.levvai.test",
        )
        self.assertTrue(delivered)
        self.assertEqual(1, len(mail.outbox))
        self.assertIn("invite_token=supplier_secret", mail.outbox[0].body)
        self.assertEqual(SupplierInvite.DELIVERY_SENT, invite.delivery_status)

    @patch("apps.accounts.supplier_contact_access.EmailMultiAlternatives.send", side_effect=RuntimeError("provider secret"))
    def test_delivery_failure_is_retryable_without_provider_details_or_token_leak(self, _send):
        invite = MagicMock(expires_at=timezone.now() + timedelta(days=7))
        delivered = _deliver(
            invite=invite,
            access=self.access,
            raw_token="supplier_secret",
            base_url="https://acme.levvai.test",
        )
        self.assertFalse(delivered)
        self.assertEqual(SupplierInvite.DELIVERY_FAILED, invite.delivery_status)
        self.assertNotIn("provider secret", invite.delivery_error)
        self.assertNotIn("supplier_secret", invite.delivery_error)

    def test_internal_worker_and_conflicting_supplier_memberships_are_rejected(self):
        user_query = MagicMock()
        user_query.first.return_value = self.user
        with patch.object(User.objects, "filter", return_value=user_query):
            worker_query = MagicMock()
            worker_query.exists.return_value = True
            with patch("apps.accounts.supplier_contact_access.WorkerProfile.objects.filter", return_value=worker_query):
                with self.assertRaisesMessage(SupplierContactAccessError, "Worker users"):
                    _validate_identity_target(tenant=self.tenant, supplier_id=7, email=self.user.email)

            worker_query.exists.return_value = False
            for membership, message in (
                (SimpleNamespace(role=Membership.ROLE_FINANCE, supplier_id=None), "internal access"),
                (SimpleNamespace(role=Membership.ROLE_SUPPLIER, supplier_id=99), "another supplier"),
            ):
                memberships = MagicMock()
                memberships.first.return_value = membership
                with (
                    patch("apps.accounts.supplier_contact_access.WorkerProfile.objects.filter", return_value=worker_query),
                    patch.object(Membership.objects, "filter", return_value=memberships),
                ):
                    with self.assertRaisesMessage(SupplierContactAccessError, message):
                        _validate_identity_target(tenant=self.tenant, supplier_id=7, email=self.user.email)

    def test_active_revoke_changes_only_exact_membership_and_advances_authorization_version(self):
        access_query = MagicMock()
        access_query.select_related.return_value.filter.return_value.first.return_value = self.access
        invites = MagicMock()
        with (
            patch("apps.accounts.supplier_contact_access.transaction.atomic", return_value=nullcontext()),
            patch.object(SupplierContactAccess.objects, "select_for_update", return_value=access_query),
            patch.object(SupplierInvite.objects, "filter", return_value=invites),
        ):
            revoked = revoke_contact_access(tenant=self.tenant, supplier_id=7, access_id=41)
        self.assertIs(revoked, self.access)
        self.assertEqual(Membership.STATUS_DEACTIVATED, self.membership.status)
        self.assertFalse(self.membership.is_active)
        self.assertEqual(5, self.membership.authorization_version)
        self.assertEqual(SupplierContactAccess.STATUS_REVOKED, self.access.status)
        invites.update.assert_called_once()

    def test_acceptance_rejects_wrong_tenant_email_expired_revoked_used_and_superseded_tokens(self):
        base = SimpleNamespace(
            tenant_id=11,
            supplier_id=7,
            email="contact@supplier.test",
            status=SupplierInvite.STATUS_PENDING,
            contact_access_id=41,
            contact_access=self.access,
            is_expired=lambda: False,
            save=MagicMock(),
        )
        cases = [
            (SimpleNamespace(**{**base.__dict__, "tenant_id": 99}), self.tenant, base.email, "does not belong"),
            (base, self.tenant, "wrong@supplier.test", "email does not match"),
            (SimpleNamespace(**{**base.__dict__, "is_expired": lambda: True}), self.tenant, base.email, "expired"),
            (SimpleNamespace(**{**base.__dict__, "status": SupplierInvite.STATUS_REVOKED}), self.tenant, base.email, "no longer active"),
            (SimpleNamespace(**{**base.__dict__, "status": SupplierInvite.STATUS_ACCEPTED}), self.tenant, base.email, "no longer active"),
            (None, self.tenant, base.email, "invalid"),
        ]
        for invitation, tenant, email, message in cases:
            with self.subTest(message=message):
                access_updates = MagicMock()
                with (
                    patch("apps.accounts.supplier_contact_access.transaction.atomic", return_value=nullcontext()),
                    patch("apps.accounts.supplier_contact_access._find_locked_invitation", return_value=invitation),
                    patch.object(SupplierContactAccess.objects, "filter", return_value=access_updates),
                ):
                    with self.assertRaisesMessage(SupplierContactAccessError, message):
                        accept_contact_invitation(
                            tenant=tenant,
                            token="supplier_secret",
                            email=email,
                            password="SafePassword!123",
                        )

    def test_acceptance_activates_the_exact_reviewed_membership(self):
        self.access.status = SupplierContactAccess.STATUS_PENDING
        self.access.accepted_at = None
        self.membership.status = Membership.STATUS_INVITED
        self.membership.is_active = True
        self.user.has_usable_password.return_value = True
        self.user.check_password.return_value = True
        invitation = SimpleNamespace(
            tenant_id=11,
            supplier_id=7,
            email=self.access.email,
            status=SupplierInvite.STATUS_PENDING,
            contact_access_id=41,
            contact_access=self.access,
            is_expired=lambda: False,
            accepted_at=None,
            accepted_by=None,
            token_hash="digest",
            token=None,
            save=MagicMock(),
        )
        with (
            patch("apps.accounts.supplier_contact_access.transaction.atomic", return_value=nullcontext()),
            patch("apps.accounts.supplier_contact_access._find_locked_invitation", return_value=invitation),
        ):
            access, membership, linked = accept_contact_invitation(
                tenant=self.tenant,
                token="supplier_secret",
                email=self.access.email,
                password="ExistingPassword!123",
            )
        self.assertIs(access, self.access)
        self.assertIs(membership, self.membership)
        self.assertTrue(linked)
        self.assertEqual(Membership.STATUS_ACTIVE, membership.status)
        self.assertEqual(SupplierContactAccess.STATUS_ACTIVE, access.status)
        self.assertEqual(SupplierInvite.STATUS_ACCEPTED, invitation.status)
        self.assertIsNone(invitation.token_hash)

    def test_resend_rotates_pending_token_and_reuses_reviewed_access_and_membership(self):
        self.access.status = SupplierContactAccess.STATUS_PENDING
        self.membership.status = Membership.STATUS_INVITED
        access_query = MagicMock()
        access_query.select_related.return_value.filter.return_value.first.return_value = self.access
        old_invites = MagicMock()
        created_invite = MagicMock()
        with (
            patch("apps.accounts.supplier_contact_access.transaction.atomic", return_value=nullcontext()),
            patch.object(SupplierContactAccess.objects, "select_for_update", return_value=access_query),
            patch.object(SupplierInvite.objects, "filter", return_value=old_invites),
            patch.object(SupplierInvite.objects, "create", return_value=created_invite) as create,
            patch("apps.accounts.supplier_contact_access.generate_token", return_value="supplier_new"),
            patch("apps.accounts.supplier_contact_access._deliver") as deliver,
        ):
            result = resend_contact_invitation(
                tenant=self.tenant,
                supplier_id=7,
                access_id=41,
                invited_by=SimpleNamespace(id=5),
                base_url="https://acme.levvai.test",
            )
        self.assertIs(result, self.access)
        old_invites.update.assert_called_once()
        self.assertEqual(token_digest("supplier_new"), create.call_args.kwargs["token_hash"])
        self.assertIs(create.call_args.kwargs["contact_access"], self.access)
        deliver.assert_called_once()
