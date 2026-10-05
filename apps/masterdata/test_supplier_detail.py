from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db.models import Q
from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import Membership
from apps.masterdata.models import Supplier
from apps.masterdata.serializers import (
    SupplierDetailSerializer,
    SupplierSerializer,
    SupplierWorkerSerializer,
)
from apps.masterdata.views import SupplierViewSet


class SupplierDetailContractTests(SimpleTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.supplier = SimpleNamespace(
            id=7,
            supplier_code="SUP-00007",
            name="Apex Talent",
            email="hello@apex.test",
            contact_name="Sam Supplier",
            contact_email="sam@apex.test",
            contact_phone="+1 555 0100",
            tax_id="TAX-7",
            diversity_status="certified",
            supplier_type="staffing",
            category="Technology",
            active_workers=2,
            active_sows=1,
            owner_name="Alex Owner",
            status="active",
            risk_level="low",
            compliance_status="compliant",
            source_system="erp",
            source_identifier="ERP-77",
            source_status="approved",
            source_last_synced_at=self.now,
            registered_address={"city": "Toronto", "country": "CA"},
            hq_country="CA",
            buying_entities=[{"id": "LE-CA", "name": "Acme Canada"}],
            business_units=[{"id": "BU-TECH", "name": "Technology"}],
            service_type="staffing",
            active_in_levv=True,
        )

    def test_detail_contract_adds_source_metadata_without_changing_supplier_identity(self):
        data = SupplierDetailSerializer(self.supplier).data

        self.assertEqual(7, data["id"])
        self.assertEqual("SUP-00007", data["supplier_id"])
        self.assertEqual("ERP-77", data["source_identifier"])
        self.assertTrue(data["active_in_levv"])
        self.assertTrue(data["source_ownership"]["read_only"])
        self.assertIn("view_workers", data["allowed_actions"])
        self.assertIn("source_status", data["source_ownership"]["fields"])

    def test_source_owned_fields_are_rejected_by_normal_supplier_writes(self):
        serializer = SupplierSerializer(
            instance=self.supplier,
            data={"source_status": "blocked"},
            partial=True,
        )

        self.assertFalse(serializer.is_valid())
        self.assertEqual(
            "This field is owned by the source system and is read-only.",
            str(serializer.errors["source_status"][0]),
        )

    def test_worker_contract_exposes_assignment_context_without_rates(self):
        assignment = SimpleNamespace(
            id=91,
            work_order_number="WO-00091",
            worker_full_name="Taylor Worker",
            worker_email="taylor@example.test",
            worker_phone="+1 555 0191",
            status="active",
            role_definition_id=3,
            role_definition=SimpleNamespace(name="Software Engineer"),
            site_id=4,
            site=SimpleNamespace(name="Toronto"),
            work_location_label="Toronto, CA",
            start_date=date(2026, 1, 5),
            end_date=None,
            created_at=self.now,
            updated_at=self.now,
        )

        data = SupplierWorkerSerializer(assignment).data

        self.assertEqual(91, data["assignment_id"])
        self.assertEqual("WO-00091", data["assignment_number"])
        self.assertEqual("Software Engineer", data["role_name"])
        self.assertNotIn("bill_rate", data)
        self.assertNotIn("pay_rate", data)


class SupplierWorkerAuthorizationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = SimpleNamespace(is_authenticated=True)
        self.tenant = SimpleNamespace(id=11, schema_name="acme")

    def request(self):
        request = self.factory.get("/api/suppliers/7/workers/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return request

    def test_internal_user_workers_query_is_scoped_by_supplier_fk_and_tenant(self):
        supplier = SimpleNamespace(id=7)
        supplier_queryset = MagicMock()
        tenant_queryset = MagicMock()
        selected_queryset = MagicMock()
        ordered_queryset = MagicMock()
        supplier_queryset.filter.return_value = tenant_queryset
        tenant_queryset.select_related.return_value = selected_queryset
        selected_queryset.order_by.return_value = ordered_queryset

        page_obj = SimpleNamespace(
            object_list=[],
            number=1,
            has_next=lambda: False,
            has_previous=lambda: False,
        )
        paginator = MagicMock(count=0, num_pages=0)
        paginator.page.return_value = page_obj

        view = SupplierViewSet.as_view({"get": "workers"})
        with (
            patch(
                "apps.common.permissions.get_active_tenant_membership",
                return_value=SimpleNamespace(role=Membership.ROLE_VIEWER),
            ),
            patch.object(SupplierViewSet, "get_object", return_value=supplier),
            patch("apps.masterdata.views.WorkOrder.objects.filter", return_value=supplier_queryset) as work_orders,
            patch("apps.masterdata.views.Paginator", return_value=paginator),
        ):
            response = view(self.request(), pk=7)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        work_orders.assert_called_once_with(supplier_id=7)
        supplier_queryset.filter.assert_called_once_with(
            Q(tenant_id=11) | Q(tenant_id__isnull=True)
        )
        self.assertEqual([], response.data["results"])

    def test_internal_user_can_retrieve_additive_supplier_detail(self):
        supplier = Supplier(id=7, supplier_code="SUP-00007", name="Apex Talent")
        view = SupplierViewSet.as_view({"get": "retrieve"})
        with (
            patch(
                "apps.common.permissions.get_active_tenant_membership",
                return_value=SimpleNamespace(role=Membership.ROLE_VIEWER),
            ),
            patch.object(SupplierViewSet, "get_object", return_value=supplier),
        ):
            response = view(self.request(), pk=7)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual("SUP-00007", response.data["supplier_id"])
        self.assertIn("source_ownership", response.data)

    def test_request_without_active_membership_cannot_probe_supplier_detail(self):
        view = SupplierViewSet.as_view({"get": "retrieve"})
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

    def test_supplier_member_cannot_read_an_internal_supplier_worker_directory(self):
        view = SupplierViewSet.as_view({"get": "workers"})
        with (
            patch(
                "apps.common.permissions.get_active_tenant_membership",
                return_value=SimpleNamespace(role=Membership.ROLE_SUPPLIER),
            ),
            patch.object(SupplierViewSet, "get_object") as get_object,
        ):
            response = view(self.request(), pk=7)

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)
        get_object.assert_not_called()
