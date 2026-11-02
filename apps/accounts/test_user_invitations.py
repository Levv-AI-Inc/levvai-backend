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
    UserInvitationAcceptView,
)
from apps.accounts.api.workos import WorkOSCallbackView
from apps.accounts.models import Membership, User, UserInvitation
from apps.accounts.user_invitations import (
    UserInvitationValidationError,
    SESSION_SSO_INVITATION_ID,
    SESSION_SSO_INVITATION_TENANT_ID,
    accept_invitation,
    build_registration_link,
    deliver_invitation,
    finalize_sso_invitation,
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

    def test_sso_token_acceptance_stages_without_activating_membership(self):
        self.user.auth_type = User.AUTH_SSO
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
        ):
            invitation, membership, linked = accept_invitation(
                tenant=self.tenant,
                token="user_test-secret",
                email=self.user.email,
            )

        self.assertIs(invitation, self.invitation)
        self.assertIs(membership, self.membership)
        self.assertFalse(linked)
        self.assertEqual(Membership.STATUS_INVITED, self.membership.status)
        self.assertEqual(UserInvitation.STATUS_PENDING, self.invitation.status)
        self.membership.save.assert_not_called()

    def test_workos_confirmation_activates_exact_sso_membership(self):
        self.user.auth_type = User.AUTH_SSO
        self.user.is_active = True
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
        ):
            invitation, membership = finalize_sso_invitation(
                tenant=self.tenant,
                invitation_id=self.invitation.id,
                email="INVITEE@ACME.TEST",
            )

        self.assertIs(invitation, self.invitation)
        self.assertIs(membership, self.membership)
        self.assertEqual(Membership.ROLE_FINANCE, membership.role)
        self.assertEqual(41, membership.business_unit_id)
        self.assertEqual(51, membership.cost_center_id)
        self.assertEqual(Membership.STATUS_ACTIVE, membership.status)
        self.assertEqual(UserInvitation.STATUS_ACCEPTED, invitation.status)
        self.assertIs(invitation.accepted_by, self.user)

    def test_workos_email_mismatch_does_not_activate_invitation(self):
        self.user.auth_type = User.AUTH_SSO
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
        ):
            with self.assertRaisesMessage(UserInvitationValidationError, "email does not match"):
                finalize_sso_invitation(
                    tenant=self.tenant,
                    invitation_id=self.invitation.id,
                    email="different@acme.test",
                )

        self.assertEqual(Membership.STATUS_INVITED, self.membership.status)
        self.assertEqual(UserInvitation.STATUS_PENDING, self.invitation.status)
        self.membership.save.assert_not_called()

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

    def test_sso_acceptance_stores_pending_invitation_in_tenant_session(self):
        request = self.factory.post(
            "/api/admin/user-invitations/user_test-secret/accept",
            {"email": "invitee@acme.test"},
            format="json",
        )
        request.tenant = self.tenant
        request.session = {}
        user = SimpleNamespace(email="invitee@acme.test", auth_type=User.AUTH_SSO)
        membership = SimpleNamespace(id=31, status=Membership.STATUS_INVITED, user=user)
        invitation = SimpleNamespace(id=61, status=UserInvitation.STATUS_PENDING)

        with patch(
            "apps.accounts.api.users.accept_invitation",
            return_value=(invitation, membership, False),
        ):
            response = UserInvitationAcceptView.as_view()(request, token="user_test-secret")

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertTrue(response.data["sso_pending"])
        self.assertEqual(Membership.STATUS_INVITED, response.data["status"])
        self.assertEqual(UserInvitation.STATUS_PENDING, response.data["invitation_status"])
        self.assertEqual(61, request.session[SESSION_SSO_INVITATION_ID])
        self.assertEqual(self.tenant.id, request.session[SESSION_SSO_INVITATION_TENANT_ID])

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


class UserInvitationWorkOSCallbackTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = SimpleNamespace(id=11, schema_name="acme")
        self.user = SimpleNamespace(
            id=21,
            email="invitee@acme.test",
            auth_type=User.AUTH_SSO,
            first_name="Invited",
            last_name="Person",
            is_active=True,
        )
        self.membership = SimpleNamespace(
            id=31,
            user=self.user,
            role=Membership.ROLE_FINANCE,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )

    @override_settings(
        WORKOS_API_KEY="test-key",
        WORKOS_CLIENT_ID="test-client",
        WORKOS_DEFAULT_NEXT_URL="/home",
    )
    def test_callback_finalizes_staged_invitation_using_verified_workos_email(self):
        request = self.factory.get(
            "/auth/workos/callback",
            {"code": "workos-code", "state": "expected-state"},
        )
        request.tenant = self.tenant
        request.session = {
            "workos_state": "expected-state",
            "workos_next": "/home",
            SESSION_SSO_INVITATION_ID: 61,
            SESSION_SSO_INVITATION_TENANT_ID: self.tenant.id,
        }
        config = SimpleNamespace(
            workos_connection_id="conn_123",
            workos_organization_id="",
            default_role=Membership.ROLE_BUSINESS,
        )
        profile = SimpleNamespace(
            email="INVITEE@ACME.TEST",
            connection_id="conn_123",
            organization_id="org_123",
            first_name="Invited",
            last_name="Person",
        )
        workos_client = MagicMock()
        workos_client.sso.get_profile_and_token.return_value = SimpleNamespace(profile=profile)

        with (
            patch("apps.accounts.api.workos.TenantSSOConfig.objects.filter") as configs,
            patch("apps.accounts.api.workos.WorkOSClient", return_value=workos_client),
            patch(
                "apps.accounts.api.workos.finalize_sso_invitation",
                return_value=(SimpleNamespace(id=61), self.membership),
            ) as finalize,
            patch("apps.accounts.api.workos.get_active_worker_profile", return_value=None),
            patch("apps.accounts.api.workos.login") as login_user,
            patch("apps.accounts.api.workos.bind_session_to_tenant") as bind_session,
            patch.object(Membership.objects, "get_or_create") as get_or_create,
        ):
            configs.return_value.first.return_value = config
            response = WorkOSCallbackView.as_view()(request)

        self.assertEqual(302, response.status_code)
        self.assertEqual("/home", response.url)
        finalize.assert_called_once_with(
            tenant=self.tenant,
            invitation_id=61,
            email="invitee@acme.test",
        )
        get_or_create.assert_not_called()
        login_user.assert_called_once()
        self.assertIs(bind_session.call_args.args[1], self.tenant)
        self.assertIs(bind_session.call_args.args[2], self.membership)
        self.assertNotIn(SESSION_SSO_INVITATION_ID, request.session)
