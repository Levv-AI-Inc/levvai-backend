from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import resolve

from apps.accounts.models import User
from apps.workorders.models import WorkOrder
from apps.workorders.serializers import WorkOrderDetailSerializer, WorkOrderListSerializer
from apps.workorders.services import WorkOrderService, WorkOrderSupplierService
from apps.workorders.views import WorkOrderSupplierAcceptView, WorkOrderSupplierRequestChangeView


class WorkOrderModelDefaultsTests(SimpleTestCase):
    def test_new_work_order_has_supplier_acceptance_defaults(self):
        work_order = WorkOrder()

        self.assertEqual(
            work_order.supplier_acceptance_status,
            WorkOrder.SUPPLIER_ACCEPTANCE_NOT_STARTED,
        )
        self.assertEqual(work_order.supplier_response_notes, "")


class WorkOrderSupplierLifecycleTests(SimpleTestCase):
    def test_final_buyer_approval_starts_supplier_acceptance(self):
        user = SimpleNamespace(
            id=7,
            username="approver@example.com",
            is_superuser=False,
            get_full_name=lambda: "Alex Approver",
        )
        work_order = MagicMock(
            pk=42,
            id=42,
            status=WorkOrder.STATUS_SUBMITTED,
            approval_status=WorkOrder.APPROVAL_PROCESSING,
            supplier_acceptance_status=WorkOrder.SUPPLIER_ACCEPTANCE_NOT_STARTED,
            approval_chain_snapshot={
                "resolved_steps": [
                    {
                        "sequence": 1,
                        "step_type": "user",
                        "approver_id": user.id,
                        "approver_name": "Alex Approver",
                        "status": "pending",
                    }
                ]
            },
        )
        queryset = MagicMock()
        queryset.get.return_value = work_order

        with (
            patch("apps.workorders.services.transaction.atomic"),
            patch.object(WorkOrder.objects, "select_for_update", return_value=queryset),
            patch.object(WorkOrderService, "_can_user_approve_step", return_value=True),
            patch.object(WorkOrderService, "_audit"),
        ):
            WorkOrderService.approve(
                tenant=SimpleNamespace(id=13),
                user=user,
                work_order=work_order,
            )

        self.assertEqual(work_order.status, WorkOrder.STATUS_APPROVED)
        self.assertEqual(work_order.approval_status, WorkOrder.APPROVAL_APPROVED)
        self.assertEqual(
            work_order.supplier_acceptance_status,
            WorkOrder.SUPPLIER_ACCEPTANCE_PENDING,
        )
        self.assertIn("supplier_acceptance_status", work_order.save.call_args.kwargs["update_fields"])

    def test_supplier_acceptance_invites_unregistered_worker(self):
        tenant = SimpleNamespace(id=13, name="Acme")
        supplier_user = SimpleNamespace(id=8)
        worker_user = SimpleNamespace(
            auth_type=User.AUTH_PASSWORD,
            has_usable_password=lambda: False,
        )
        worker_profile = SimpleNamespace(id=21, user=worker_user)
        engagement = SimpleNamespace(id=31, worker_profile=worker_profile)
        work_order = MagicMock(
            pk=42,
            id=42,
            status=WorkOrder.STATUS_APPROVED,
            supplier_acceptance_status=WorkOrder.SUPPLIER_ACCEPTANCE_PENDING,
            worker_email="worker@example.com",
            worker_full_name="Jamie Worker",
            source_snapshot={},
        )
        queryset = MagicMock()
        queryset.select_related.return_value = queryset
        queryset.get.return_value = work_order
        profile_queryset = MagicMock()
        profile_queryset.order_by.return_value.first.return_value = None

        with (
            patch("apps.workorders.services.transaction.atomic"),
            patch.object(WorkOrder.objects, "select_for_update", return_value=queryset),
            patch("apps.accounts.models.WorkerProfile.objects.filter", return_value=profile_queryset),
            patch(
                "apps.accounts.worker_accounts.ensure_worker_engagement_for_work_order",
                return_value=engagement,
            ) as ensure_engagement,
            patch("apps.accounts.worker_accounts.issue_worker_invite") as issue_invite,
            patch("apps.accounts.worker_accounts.activate_worker_engagement") as activate_engagement,
            patch.object(WorkOrderService, "_audit"),
        ):
            acceptance = WorkOrderSupplierService.accept(
                tenant=tenant,
                user=supplier_user,
                work_order=work_order,
                base_url="https://acme.levvai.com",
            )

        ensure_engagement.assert_called_once_with(
            tenant=tenant,
            work_order=work_order,
            invited_by=supplier_user,
            activate=False,
        )
        issue_invite.assert_called_once_with(
            tenant=tenant,
            work_order=work_order,
            worker_profile=worker_profile,
            invited_by=supplier_user,
            base_url="https://acme.levvai.com",
            send_email=True,
        )
        activate_engagement.assert_not_called()
        self.assertTrue(acceptance.worker_is_new)
        self.assertTrue(acceptance.registration_required)
        self.assertEqual(work_order.status, WorkOrder.STATUS_ACTIVE)
        self.assertEqual(
            work_order.supplier_acceptance_status,
            WorkOrder.SUPPLIER_ACCEPTANCE_ACCEPTED,
        )

    def test_supplier_acceptance_does_not_email_registered_worker(self):
        tenant = SimpleNamespace(id=13, name="Acme")
        supplier_user = SimpleNamespace(id=8)
        worker_user = SimpleNamespace(
            auth_type=User.AUTH_PASSWORD,
            has_usable_password=lambda: True,
        )
        worker_profile = SimpleNamespace(id=21, user=worker_user)
        engagement = SimpleNamespace(id=31, worker_profile=worker_profile)
        work_order = MagicMock(
            pk=42,
            id=42,
            status=WorkOrder.STATUS_APPROVED,
            supplier_acceptance_status=WorkOrder.SUPPLIER_ACCEPTANCE_PENDING,
            worker_email="worker@example.com",
            worker_full_name="Jamie Worker",
            source_snapshot={},
        )
        queryset = MagicMock()
        queryset.select_related.return_value = queryset
        queryset.get.return_value = work_order
        profile_queryset = MagicMock()
        profile_queryset.order_by.return_value.first.return_value = worker_profile

        with (
            patch("apps.workorders.services.transaction.atomic"),
            patch.object(WorkOrder.objects, "select_for_update", return_value=queryset),
            patch("apps.accounts.models.WorkerProfile.objects.filter", return_value=profile_queryset),
            patch(
                "apps.accounts.worker_accounts.ensure_worker_engagement_for_work_order",
                return_value=engagement,
            ),
            patch("apps.accounts.worker_accounts.issue_worker_invite") as issue_invite,
            patch("apps.accounts.worker_accounts.activate_worker_engagement") as activate_engagement,
            patch.object(WorkOrderService, "_audit"),
        ):
            acceptance = WorkOrderSupplierService.accept(
                tenant=tenant,
                user=supplier_user,
                work_order=work_order,
                base_url="https://acme.levvai.com",
            )

        issue_invite.assert_not_called()
        activate_engagement.assert_called_once_with(engagement)
        self.assertFalse(acceptance.worker_is_new)
        self.assertFalse(acceptance.registration_required)

    def test_supplier_acceptance_routes_and_fields_are_exposed(self):
        self.assertIs(
            resolve("/api/work-orders/42/accept").func.view_class,
            WorkOrderSupplierAcceptView,
        )
        self.assertIs(
            resolve("/api/work-orders/42/request-change").func.view_class,
            WorkOrderSupplierRequestChangeView,
        )
        self.assertIn("supplier_acceptance_status", WorkOrderListSerializer.Meta.fields)
        for field in (
            "supplier_acceptance_status",
            "supplier_response_notes",
            "supplier_accepted_at",
            "supplier_accepted_by",
            "supplier_change_requested_at",
            "supplier_change_requested_by",
        ):
            self.assertIn(field, WorkOrderDetailSerializer.Meta.fields)
