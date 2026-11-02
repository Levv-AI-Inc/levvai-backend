from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import Membership
from apps.masterdata.models import SupplierCoverage
from apps.masterdata.views import SupplierViewSet


def coverage_record(*, active=True):
    now = timezone.now()
    return SimpleNamespace(
        id=31,
        supplier_id=7,
        role_id=3,
        role=SimpleNamespace(
            code="software-engineer-ca",
            name="Software Engineer",
            location_label="Canada",
        ),
        site_id=4,
        site=SimpleNamespace(
            code="TOR-01",
            name="Toronto",
            city="Toronto",
            country="CA",
        ),
        is_active=active,
        created_at=now,
        updated_at=now,
        save=MagicMock(),
    )


class SupplierCoverageContractTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(is_authenticated=True)
        self.tenant = SimpleNamespace(id=11, schema_name="acme")
        self.supplier = SimpleNamespace(id=7)

    def request(self, method="get", path="/api/suppliers/7/coverage/", data=None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return request

    def membership(self, role):
        return patch(
            "apps.common.permissions.get_active_tenant_membership",
            return_value=SimpleNamespace(role=role),
        )

    def test_model_enforces_one_supplier_role_site_tuple(self):
        constraints = {constraint.name: constraint for constraint in SupplierCoverage._meta.constraints}
        constraint = constraints["supplier_coverage_unique_role_site"]
        self.assertEqual(("supplier", "role", "site"), tuple(constraint.fields))

    def test_internal_read_is_scoped_to_supplier_from_url(self):
        queryset = MagicMock()
        selected = MagicMock()
        ordered = MagicMock()
        queryset.select_related.return_value = selected
        selected.order_by.return_value = ordered
        ordered.__iter__.return_value = iter([])
        view = SupplierViewSet.as_view({"get": "coverage"})

        with (
            self.membership(Membership.ROLE_VIEWER),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch("apps.masterdata.views.SupplierCoverage.objects.filter", return_value=queryset) as records,
        ):
            response = view(self.request(), pk=7)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        records.assert_called_once_with(supplier=self.supplier)
        self.assertEqual([], response.data["results"])

    def test_non_administrator_cannot_create_coverage(self):
        view = SupplierViewSet.as_view({"post": "coverage"})
        with (
            self.membership(Membership.ROLE_MANAGER),
            patch.object(SupplierViewSet, "get_object") as get_object,
        ):
            response = view(self.request("post", data={"role_id": 3, "site_id": 4}), pk=7)

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)
        get_object.assert_not_called()

    def test_supplier_membership_cannot_read_coverage(self):
        view = SupplierViewSet.as_view({"get": "coverage"})
        with (
            self.membership(Membership.ROLE_SUPPLIER),
            patch.object(SupplierViewSet, "get_object") as get_object,
        ):
            response = view(self.request(), pk=7)

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)
        get_object.assert_not_called()

    def test_request_without_active_tenant_membership_cannot_probe_coverage(self):
        view = SupplierViewSet.as_view({"get": "coverage"})
        with (
            patch(
                "apps.common.permissions.get_active_tenant_membership",
                return_value=None,
            ),
            patch.object(SupplierViewSet, "get_object") as get_object,
        ):
            response = view(self.request(), pk=7)

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)
        get_object.assert_not_called()

    def test_inactive_duplicate_is_reactivated_without_creating_a_second_row(self):
        existing = coverage_record(active=False)
        role = SimpleNamespace(id=3)
        site = SimpleNamespace(id=4)
        payload = MagicMock(validated_data={"role": role, "site": site})
        locked = MagicMock()
        locked.filter.return_value.first.return_value = existing
        view = SupplierViewSet.as_view({"post": "coverage"})

        with (
            self.membership(Membership.ROLE_ADMIN),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch("apps.masterdata.views.SupplierCoverageCreateSerializer", return_value=payload),
            patch("apps.masterdata.views.transaction.atomic", side_effect=lambda: nullcontext()),
            patch("apps.masterdata.views.SupplierCoverage.objects.select_for_update", return_value=locked),
            patch("apps.masterdata.views.SupplierCoverage.objects.create") as create,
        ):
            response = view(self.request("post", data={"role_id": 3, "site_id": 4}), pk=7)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertTrue(existing.is_active)
        existing.save.assert_called_once_with(update_fields=["is_active", "updated_at"])
        create.assert_not_called()
        self.assertEqual(31, response.data["id"])

    def test_active_duplicate_returns_conflict(self):
        existing = coverage_record(active=True)
        payload = MagicMock(
            validated_data={"role": SimpleNamespace(id=3), "site": SimpleNamespace(id=4)}
        )
        locked = MagicMock()
        locked.filter.return_value.first.return_value = existing
        view = SupplierViewSet.as_view({"post": "coverage"})

        with (
            self.membership(Membership.ROLE_ADMIN),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch("apps.masterdata.views.SupplierCoverageCreateSerializer", return_value=payload),
            patch("apps.masterdata.views.transaction.atomic", side_effect=lambda: nullcontext()),
            patch("apps.masterdata.views.SupplierCoverage.objects.select_for_update", return_value=locked),
        ):
            response = view(self.request("post", data={"role_id": 3, "site_id": 4}), pk=7)

        self.assertEqual(status.HTTP_409_CONFLICT, response.status_code)

    def test_update_cannot_address_coverage_from_another_supplier(self):
        payload = MagicMock(validated_data={"is_active": False})
        scoped = MagicMock()
        scoped.first.return_value = None
        view = SupplierViewSet.as_view({"patch": "coverage_update"})

        with (
            self.membership(Membership.ROLE_ADMIN),
            patch.object(SupplierViewSet, "get_object", return_value=self.supplier),
            patch("apps.masterdata.views.SupplierCoverageUpdateSerializer", return_value=payload),
            patch("apps.masterdata.views.SupplierCoverage.objects.filter", return_value=scoped) as records,
        ):
            response = view(
                self.request("patch", "/api/suppliers/7/coverage/99/", {"is_active": False}),
                pk=7,
                coverage_id=99,
            )

        self.assertEqual(status.HTTP_404_NOT_FOUND, response.status_code)
        records.assert_called_once_with(id=99, supplier=self.supplier)
