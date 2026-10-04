from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.urls import resolve
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate
from django.test import SimpleTestCase

from apps.accounts.api.session import SessionStatusView
from apps.accounts.api.users import AdminUserListView
from apps.accounts.models import Membership
from apps.masterdata.views import SupplierViewSet
from apps.rates.views import RateCardViewSet


class _MembershipQuery:
    def __init__(self, memberships):
        self.memberships = memberships
        self.filters = []

    def filter(self, *args, **kwargs):
        self.filters.append((args, kwargs))
        return self

    def exclude(self, *args, **kwargs):
        return self

    def select_related(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def __getitem__(self, item):
        return self.memberships[item]


class IntegrationContractTests(SimpleTestCase):
    """Freeze the additive baseline consumed by the Next.js API clients."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = SimpleNamespace(id=42, schema_name="acme", name="Acme")
        self.user = SimpleNamespace(
            id=7,
            email="admin@acme.test",
            first_name="Ada",
            last_name="Admin",
            is_authenticated=True,
        )

    def request(self, path, query=None):
        request = self.factory.get(path, query or {})
        request.tenant = self.tenant
        request.user = self.user
        request.session = {}
        force_authenticate(request, user=self.user)
        return request

    def test_session_contract_for_bound_internal_membership(self):
        membership = SimpleNamespace(
            id=19,
            role=Membership.ROLE_ADMIN,
            tenant_id=self.tenant.id,
            status=Membership.STATUS_ACTIVE,
            authorization_version=3,
        )
        request = self.request("/api/session")

        with (
            patch("apps.accounts.api.session.get_active_tenant_membership", return_value=membership),
            patch("apps.accounts.api.session.get_active_worker_profile", return_value=None),
            patch("apps.accounts.api.session.is_session_bound_to_tenant", return_value=True),
        ):
            response = SessionStatusView.as_view()(request)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual(
            {"authenticated", "user", "profile", "membership"},
            set(response.data),
        )
        self.assertEqual(
            {"id", "email", "first_name", "last_name"},
            set(response.data["user"]),
        )
        self.assertEqual(
            response.data["membership"],
            {key: value for key, value in response.data["profile"].items() if key != "type"},
        )
        self.assertEqual("internal", response.data["profile"]["type"])
        self.assertEqual(Membership.ROLE_ADMIN, response.data["membership"]["role"])
        self.assertEqual(self.tenant.id, response.data["membership"]["tenant_id"])
        self.assertEqual(19, response.data["membership"]["membership_id"])
        self.assertEqual(Membership.STATUS_ACTIVE, response.data["membership"]["state"])
        self.assertEqual(3, response.data["membership"]["authorization_version"])

    def test_admin_users_contract_preserves_results_wrapper_and_fields(self):
        account = SimpleNamespace(
            id=8,
            email="pat@acme.test",
            username="pat@acme.test",
            auth_type="password",
            get_full_name=lambda: "Pat Manager",
        )
        membership = SimpleNamespace(
            id=21,
            user=account,
            role=Membership.ROLE_MANAGER,
            status=Membership.STATUS_ACTIVE,
            business_unit_id=None,
            cost_center_id=None,
            is_active=True,
        )
        query = _MembershipQuery([membership])
        request = self.request(
            "/api/admin/users",
            {"search": "Pat", "status": "active", "role": "manager"},
        )

        with patch("apps.accounts.api.users.Membership.objects.filter", return_value=query):
            response = AdminUserListView().get(request)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual({"results"}, set(response.data))
        self.assertEqual(1, len(response.data["results"]))
        self.assertEqual(
            {
                "membership_id",
                "user_id",
                "name",
                "email",
                "status",
                "role",
                "business_unit_id",
                "business_unit",
                "cost_center_id",
                "cost_center",
                "cost_center_name",
                "sso_enabled",
                "is_active",
            },
            set(response.data["results"][0]),
        )
        self.assertEqual(21, response.data["results"][0]["membership_id"])
        self.assertTrue(any(kwargs == {"role": "manager"} for _, kwargs in query.filters))
        self.assertTrue(any(kwargs == {"status": "active"} for _, kwargs in query.filters))

    def test_supplier_list_contract_is_an_unpaginated_array(self):
        supplier = SimpleNamespace(
            id=3,
            supplier_code="SUP-003",
            name="Apex Talent",
            email="hello@apex.test",
            contact_name="Sam Supplier",
            contact_email="sam@apex.test",
            contact_phone="+1 555 0100",
            tax_id="TAX-3",
            diversity_status="certified",
            supplier_type="staffing",
            category="Technology",
            active_workers=4,
            active_sows=2,
            owner_name="Alex Owner",
            status="active",
            risk_level="low",
            compliance_status="compliant",
        )
        request = self.request("/api/suppliers/")
        view = SupplierViewSet()
        view.request = request
        view.action = "list"
        view.args = ()
        view.kwargs = {}
        view.format_kwarg = None

        with patch.object(view, "get_queryset", return_value=[supplier]):
            response = view.list(request)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertIsInstance(response.data, list)
        self.assertEqual("SUP-003", response.data[0]["supplier_id"])
        self.assertEqual(
            {
                "id",
                "supplier_id",
                "supplier_code",
                "name",
                "email",
                "contact_name",
                "contact_email",
                "contact_phone",
                "tax_id",
                "diversity_status",
                "supplier_type",
                "category",
                "active_workers",
                "active_sows",
                "owner_name",
                "status",
                "risk_level",
                "compliance_status",
            },
            set(response.data[0]),
        )

    def test_rate_card_list_contract_uses_supported_rates_model_shape(self):
        now = timezone.now()
        card = SimpleNamespace(
            id=5,
            name="Engineering 2026",
            role_definition=SimpleNamespace(pk=11, name="Software Engineer"),
            role_definition_id=11,
            currency="CAD",
            unit="hour",
            effective_date=date(2026, 1, 1),
            end_date=None,
            rate_structure=SimpleNamespace(pk=13, name="Bill Rate"),
            rate_structure_id=13,
            status="draft",
            created_at=now,
            updated_at=now,
        )
        request = self.request("/api/rate-cards/")
        view = RateCardViewSet()
        view.request = request
        view.action = "list"
        view.args = ()
        view.kwargs = {}
        view.format_kwarg = None

        with patch.object(view, "get_queryset", return_value=[card]):
            response = view.list(request)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertIsInstance(response.data, list)
        self.assertEqual(
            {
                "id",
                "name",
                "role_definition",
                "role_name",
                "currency",
                "unit",
                "effective_date",
                "end_date",
                "rate_structure",
                "rate_structure_name",
                "status",
                "created_at",
                "updated_at",
            },
            set(response.data[0]),
        )
        self.assertEqual("Software Engineer", response.data[0]["role_name"])
        self.assertNotIn("lines", response.data[0])

    def test_rate_card_route_resolves_to_supported_rates_viewset(self):
        match = resolve("/api/rate-cards/")

        self.assertIs(match.func.cls, RateCardViewSet)

    def test_contract_endpoints_keep_existing_role_gates(self):
        manager_membership = SimpleNamespace(role=Membership.ROLE_MANAGER)
        viewer_membership = SimpleNamespace(role=Membership.ROLE_VIEWER)
        supplier_membership = SimpleNamespace(role=Membership.ROLE_SUPPLIER)

        with patch("apps.common.permissions.get_active_tenant_membership", return_value=manager_membership):
            users_response = AdminUserListView.as_view()(self.request("/api/admin/users"))
        with patch("apps.common.permissions.get_active_tenant_membership", return_value=viewer_membership):
            rates_response = RateCardViewSet.as_view({"get": "list"})(self.request("/api/rate-cards/"))
        with patch("apps.common.permissions.get_active_tenant_membership", return_value=supplier_membership):
            suppliers_response = SupplierViewSet.as_view({"get": "list"})(self.request("/api/suppliers/"))

        self.assertEqual(status.HTTP_403_FORBIDDEN, users_response.status_code)
        self.assertEqual(status.HTTP_403_FORBIDDEN, rates_response.status_code)
        self.assertEqual(status.HTTP_403_FORBIDDEN, suppliers_response.status_code)
