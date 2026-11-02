from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.api.users import AdminUserDetailView
from apps.accounts.membership_lifecycle import (
    apply_membership_authorization_changes,
    normalize_membership_status,
    validate_final_active_administrator,
)
from apps.accounts.models import Membership, User
from apps.accounts.session_scope import (
    SESSION_AUTHORIZATION_VERSION_KEY,
    SESSION_MEMBERSHIP_ID_KEY,
    SESSION_TENANT_ID_KEY,
    bind_session_to_tenant,
    is_session_bound_to_tenant,
)


class _Session(dict):
    modified = False


class MembershipLifecycleUnitTests(SimpleTestCase):
    def test_disabled_is_retained_as_a_legacy_alias_for_suspended(self):
        self.assertEqual(Membership.STATUS_SUSPENDED, normalize_membership_status("disabled"))

    def test_access_changes_increment_authorization_version_once(self):
        membership = SimpleNamespace(
            role=Membership.ROLE_BUSINESS,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
            business_unit_id=10,
            supplier_id=None,
            authorization_version=6,
        )

        changed = apply_membership_authorization_changes(
            membership,
            role=Membership.ROLE_FINANCE,
            status=Membership.STATUS_SUSPENDED,
            business_unit_id=11,
        )

        self.assertTrue(changed)
        self.assertEqual(7, membership.authorization_version)
        self.assertEqual(Membership.STATUS_SUSPENDED, membership.status)
        self.assertFalse(membership.is_active)

    def test_last_active_administrator_cannot_be_suspended_or_demoted(self):
        membership = SimpleNamespace(
            id=9,
            tenant_id=4,
            role=Membership.ROLE_ADMIN,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )
        query = MagicMock()
        query.exclude.return_value.exists.return_value = False

        with patch.object(Membership.objects, "filter", return_value=query):
            with self.assertRaisesMessage(ValidationError, "final active customer administrator"):
                validate_final_active_administrator(
                    membership,
                    new_role=Membership.ROLE_ADMIN,
                    new_status=Membership.STATUS_SUSPENDED,
                )

    def test_another_active_administrator_allows_lifecycle_change(self):
        membership = SimpleNamespace(
            id=9,
            tenant_id=4,
            role=Membership.ROLE_ADMIN,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )
        query = MagicMock()
        query.exclude.return_value.exists.return_value = True

        with patch.object(Membership.objects, "filter", return_value=query):
            validate_final_active_administrator(
                membership,
                new_role=Membership.ROLE_READ_ONLY,
                new_status=Membership.STATUS_ACTIVE,
            )


class MembershipSessionBindingTests(SimpleTestCase):
    def setUp(self):
        self.tenant = SimpleNamespace(id=4)
        self.membership = SimpleNamespace(id=9, tenant_id=4, authorization_version=3)
        self.request = SimpleNamespace(session=_Session())

    def test_binding_records_membership_and_authorization_version(self):
        bind_session_to_tenant(self.request, self.tenant, self.membership)

        self.assertEqual(4, self.request.session[SESSION_TENANT_ID_KEY])
        self.assertEqual(9, self.request.session[SESSION_MEMBERSHIP_ID_KEY])
        self.assertEqual(3, self.request.session[SESSION_AUTHORIZATION_VERSION_KEY])

    def test_version_change_invalidates_only_the_bound_membership_session(self):
        bind_session_to_tenant(self.request, self.tenant, self.membership)
        self.membership.authorization_version = 4

        self.assertFalse(
            is_session_bound_to_tenant(self.request, self.tenant, self.membership)
        )

        unrelated = SimpleNamespace(id=12, tenant_id=5, authorization_version=1)
        unrelated_request = SimpleNamespace(session=_Session())
        bind_session_to_tenant(unrelated_request, SimpleNamespace(id=5), unrelated)
        self.assertTrue(
            is_session_bound_to_tenant(
                unrelated_request,
                SimpleNamespace(id=5),
                unrelated,
            )
        )

    def test_legacy_tenant_binding_is_upgraded_without_forcing_logout(self):
        self.request.session[SESSION_TENANT_ID_KEY] = self.tenant.id

        self.assertTrue(
            is_session_bound_to_tenant(self.request, self.tenant, self.membership)
        )
        self.assertEqual(9, self.request.session[SESSION_MEMBERSHIP_ID_KEY])
        self.assertEqual(3, self.request.session[SESSION_AUTHORIZATION_VERSION_KEY])


class AdminMembershipLifecycleEndpointTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = SimpleNamespace(id=4, schema_name="acme")
        self.actor = SimpleNamespace(is_authenticated=True)

    def request(self, data):
        request = self.factory.patch("/api/admin/users/9", data, format="json")
        request.tenant = self.tenant
        request.user = self.actor
        request.session = {}
        force_authenticate(request, user=self.actor)
        return request

    def test_cross_tenant_membership_id_is_not_found(self):
        query = MagicMock()
        query.exclude.return_value.select_related.return_value.first.return_value = None

        with (
            patch(
                "apps.common.permissions.get_active_tenant_membership",
                return_value=SimpleNamespace(role=Membership.ROLE_ADMIN),
            ),
            patch.object(Membership.objects, "filter", return_value=query) as membership_filter,
        ):
            response = AdminUserDetailView.as_view()(
                self.request({"status": "suspended"}), membership_id=9
            )

        self.assertEqual(status.HTTP_404_NOT_FOUND, response.status_code)
        membership_filter.assert_called_once()
        self.assertEqual(self.tenant, membership_filter.call_args.kwargs["tenant"])

    def test_suspending_membership_does_not_disable_shared_user(self):
        user = MagicMock(
            id=7,
            email="shared@example.com",
            username="shared@example.com",
            first_name="Shared",
            last_name="User",
            auth_type="password",
            is_active=True,
        )
        user.get_full_name.return_value = "Shared User"
        membership = MagicMock(
            id=9,
            tenant_id=4,
            tenant=self.tenant,
            user=user,
            user_id=7,
            role=Membership.ROLE_BUSINESS,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
            business_unit_id=None,
            cost_center_id=None,
            supplier_id=None,
            authorization_version=2,
        )
        query = MagicMock()
        query.exclude.return_value.select_related.return_value.first.return_value = membership
        user_query = MagicMock()
        user_query.exclude.return_value.exists.return_value = False

        with (
            patch(
                "apps.common.permissions.get_active_tenant_membership",
                return_value=SimpleNamespace(role=Membership.ROLE_ADMIN),
            ),
            patch.object(Membership.objects, "filter", return_value=query),
            patch.object(User.objects, "filter", return_value=user_query),
            patch("apps.accounts.api.users.transaction.atomic"),
            patch(
                "apps.accounts.api.users._admin_membership_payload",
                return_value={"status": "suspended"},
            ),
        ):
            response = AdminUserDetailView.as_view()(
                self.request({"status": "disabled"}), membership_id=9
            )

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertTrue(user.is_active)
        self.assertEqual(Membership.STATUS_SUSPENDED, membership.status)
        self.assertFalse(membership.is_active)
        self.assertEqual(3, membership.authorization_version)
