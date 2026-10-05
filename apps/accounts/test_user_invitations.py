from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.api.users import (
    AdminUserDetailView,
    AdminUserInvitationResendView,
    AdminUserListView,
    UserRegisterView,
)
from apps.accounts.models import Membership, User, UserInvitation
from apps.accounts.user_invitations import (
    UserInvitationValidationError,
    accept_invitation,
    build_registration_link,
    deliver_invitation,
    resend_invitation,
    revoke_invitation,
    token_digest,
)


def invitation_lookup(invitation):
    query = MagicMock()
    query.select_related.return_value.filter.return_value.first.return_value = invitation
    return query


class UserInvitationServiceTests(SimpleTestCase):
    def setUp(self):
        self.tenant = SimpleNamespace(id=11, name="Acme", schema_name="acme")
        self.user = MagicMock(
            id=21,
            email="invitee@acme.test",
            auth_type=User.AUTH_PASSWORD,
        )
        self.user.get_full_name.return_value = "Invited Person"
        self.membership = MagicMock(
            id=31,
            tenant_id=self.tenant.id,
            tenant=self.tenant,
            user=self.user,
            role=Membership.ROLE_FINANCE,
            status=Membership.STATUS_INVITED,
            is_active=False,
            business_unit_id=41,
            cost_center_id=51,
            authorization_version=1,
        )
        self.invitation = MagicMock(
            id=61,
            tenant_id=self.tenant.id,
            tenant=self.tenant,
            membership_id=self.membership.id,
            membership=self.membership,
            email=self.user.email,
            status=UserInvitation.STATUS_PENDING,
            delivery_status=UserInvitation.DELIVERY_PENDING,
            delivery_error="",
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.invitation.is_expired.return_value = False

    def test_tokens_are_hashed_and_registration_link_keeps_raw_token_out_of_model_fields(self):
        raw_token = "user_test-secret"
        link = build_registration_link(
            base_url="https://acme.levvai.test/",
            invitation=self.invitation,
            raw_token=raw_token,
        )

        self.assertEqual(64, len(token_digest(raw_token)))
        self.assertNotEqual(raw_token, token_digest(raw_token))
        self.assertIn("invite_token=user_test-secret", link)
        self.assertIn("invitation_type=user", link)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        USER_INVITE_FROM_EMAIL="users@levvai.test",
    )
    def test_delivery_uses_test_backend_and_contains_single_use_link(self):
        mail.outbox = []

        delivered = deliver_invitation(
            invitation=self.invitation,
            raw_token="user_test-secret",
            base_url="https://acme.levvai.test",
        )

        self.assertTrue(delivered)
        self.assertEqual(1, len(mail.outbox))
        self.assertEqual([self.user.email], mail.outbox[0].to)
        self.assertIn("invite_token=user_test-secret", mail.outbox[0].body)
        self.assertEqual(UserInvitation.DELIVERY_SENT, self.invitation.delivery_status)

    @patch("apps.accounts.user_invitations.EmailMultiAlternatives.send", side_effect=RuntimeError("mail down"))
    def test_failed_delivery_is_recorded_for_retry_without_exposing_provider_error(self, _send):
        delivered = deliver_invitation(
            invitation=self.invitation,
            raw_token="user_test-secret",
            base_url="https://acme.levvai.test",
        )

        self.assertFalse(delivered)
        self.assertEqual(UserInvitation.DELIVERY_FAILED, self.invitation.delivery_status)
        self.assertNotIn("mail down", self.invitation.delivery_error)
        self.invitation.save.assert_called_once()

    def test_acceptance_activates_exact_reviewed_membership_and_replay_is_blocked(self):
        self.user.has_usable_password.return_value = False
        membership_query = MagicMock()
        membership_query.get.return_value = self.membership

        with (
            patch("apps.accounts.user_invitations.transaction.atomic"),
            patch.object(
                UserInvitation.objects,
                "select_for_update",
                return_value=invitation_lookup(self.invitation),
            ),
            patch.object(Membership.objects, "select_for_update", return_value=membership_query),
            patch("apps.accounts.user_invitations.validate_password_policy") as validate_password,
            patch("apps.accounts.user_invitations.record_password_history") as record_history,
        ):
            _, accepted_membership, linked = accept_invitation(
                tenant=self.tenant,
                token="user_test-secret",
                email=self.user.email,
                password="SafePassword!123",
            )

            self.assertIs(accepted_membership, self.membership)
            self.assertFalse(linked)
            self.assertEqual(Membership.ROLE_FINANCE, self.membership.role)
            self.assertEqual(41, self.membership.business_unit_id)
            self.assertEqual(51, self.membership.cost_center_id)
            self.assertEqual(Membership.STATUS_ACTIVE, self.membership.status)
            validate_password.assert_called_once()
            record_history.assert_called_once_with(self.user, self.tenant)
            self.assertEqual(UserInvitation.STATUS_ACCEPTED, self.invitation.status)

            with self.assertRaisesMessage(UserInvitationValidationError, "no longer active"):
                accept_invitation(
                    tenant=self.tenant,
                    token="user_test-secret",
                    email=self.user.email,
                    password="SafePassword!123",
                )

    def test_expired_revoked_and_wrong_tenant_invitations_are_denied(self):
        cases = [
            (True, UserInvitation.STATUS_PENDING, self.tenant, "expired"),
            (False, UserInvitation.STATUS_REVOKED, self.tenant, "no longer active"),
            (False, UserInvitation.STATUS_PENDING, SimpleNamespace(id=99), "does not belong"),
        ]
        for expired, invitation_status, tenant, message in cases:
            with self.subTest(message=message):
                self.invitation.status = invitation_status
                self.invitation.is_expired.return_value = expired
                with (
                    patch("apps.accounts.user_invitations.transaction.atomic"),
                    patch.object(
                        UserInvitation.objects,
                        "select_for_update",
                        return_value=invitation_lookup(self.invitation),
                    ),
                ):
                    with self.assertRaisesMessage(UserInvitationValidationError, message):
                        accept_invitation(
                            tenant=tenant,
                            token="user_test-secret",
                            email=self.user.email,
                            password="SafePassword!123",
                        )

    def test_resend_rotates_token_and_revoke_invalidates_invitation(self):
        self.invitation.token_hash = token_digest("user_old")
        self.membership.status = Membership.STATUS_INVITED

        with (
            patch("apps.accounts.user_invitations.transaction.atomic"),
            patch.object(
                UserInvitation.objects,
                "select_for_update",
                return_value=invitation_lookup(self.invitation),
            ),
            patch("apps.accounts.user_invitations.generate_token", return_value="user_new"),
            patch("apps.accounts.user_invitations.deliver_invitation") as deliver,
        ):
            resent = resend_invitation(
                invitation_id=self.invitation.id,
                tenant=self.tenant,
                invited_by=SimpleNamespace(id=7),
                base_url="https://acme.levvai.test",
            )

        self.assertIs(resent, self.invitation)
        self.assertEqual(token_digest("user_new"), self.invitation.token_hash)
        self.assertNotEqual(token_digest("user_old"), self.invitation.token_hash)
        deliver.assert_called_once()

        with (
            patch("apps.accounts.user_invitations.transaction.atomic"),
            patch.object(
                UserInvitation.objects,
                "select_for_update",
                return_value=invitation_lookup(self.invitation),
            ),
        ):
            revoked = revoke_invitation(invitation_id=61, tenant=self.tenant)
        self.assertEqual(UserInvitation.STATUS_REVOKED, revoked.status)


class UserInvitationEndpointSecurityTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = SimpleNamespace(id=11, name="Acme", schema_name="acme")
        self.actor = SimpleNamespace(id=7, is_authenticated=True)

    def test_duplicate_membership_is_rejected_by_compatibility_wrapper(self):
        request = self.factory.post(
            "/api/admin/users",
            {"name": "Existing User", "email": "existing@acme.test", "role": "viewer"},
            format="json",
        )
        request.tenant = self.tenant
        request.user = self.actor
        force_authenticate(request, user=self.actor)
        user = SimpleNamespace(id=21, auth_type=User.AUTH_PASSWORD)
        user_query = MagicMock()
        user_query.first.return_value = user
        membership_query = MagicMock()
        membership_query.exists.return_value = True

        with (
            patch("apps.common.permissions.get_active_tenant_membership", return_value=SimpleNamespace(role="admin")),
            patch.object(User.objects, "filter", return_value=user_query),
            patch.object(Membership.objects, "filter", return_value=membership_query),
            patch("apps.accounts.api.users.WorkerProfile.objects.filter") as workers,
        ):
            workers.return_value.exists.return_value = False
            response = AdminUserListView.as_view()(request)

        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertIn("already belongs", response.data["detail"])

    def test_non_administrator_cannot_resend_invitation(self):
        request = self.factory.post("/api/admin/user-invitations/61/resend", {}, format="json")
        request.tenant = self.tenant
        request.user = self.actor
        force_authenticate(request, user=self.actor)

        with patch(
            "apps.common.permissions.get_active_tenant_membership",
            return_value=SimpleNamespace(role=Membership.ROLE_MANAGER),
        ):
            response = AdminUserInvitationResendView.as_view()(request, invitation_id=61)

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)

    def test_pending_invitation_access_cannot_be_changed_through_legacy_patch(self):
        request = self.factory.patch(
            "/api/admin/users/31",
            {"role": Membership.ROLE_ADMIN, "status": Membership.STATUS_ACTIVE},
            format="json",
        )
        request.tenant = self.tenant
        request.user = self.actor
        force_authenticate(request, user=self.actor)

        with (
            patch("apps.common.permissions.get_active_tenant_membership", return_value=SimpleNamespace(role="admin")),
            patch("apps.accounts.api.users._get_admin_membership", return_value=SimpleNamespace(id=31)),
            patch(
                "apps.accounts.api.users._membership_invitation",
                return_value=SimpleNamespace(status=UserInvitation.STATUS_PENDING),
            ),
        ):
            response = AdminUserDetailView.as_view()(request, membership_id=31)

        self.assertEqual(status.HTTP_409_CONFLICT, response.status_code)
        self.assertIn("locked", response.data["detail"])

    def test_generic_registration_cannot_claim_an_invited_membership(self):
        request = self.factory.post(
            "/auth/password/register-user",
            {
                "email": "invitee@acme.test",
                "password": "SafePassword!123",
                "first_name": "Invited",
                "last_name": "Person",
            },
            format="json",
        )
        request.tenant = self.tenant
        user = SimpleNamespace(auth_type=User.AUTH_PASSWORD)
        user_query = MagicMock()
        user_query.first.return_value = user
        worker_query = MagicMock()
        worker_query.first.return_value = None
        membership_query = MagicMock()
        membership_query.first.return_value = SimpleNamespace(
            role=Membership.ROLE_READ_ONLY,
            status=Membership.STATUS_INVITED,
        )

        with (
            patch.object(User.objects, "filter", return_value=user_query),
            patch("apps.accounts.api.users.WorkerProfile.objects.filter", return_value=worker_query),
            patch.object(Membership.objects, "filter", return_value=membership_query),
        ):
            response = UserRegisterView.as_view()(request)

        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertIn("invitation link", response.data["detail"])
