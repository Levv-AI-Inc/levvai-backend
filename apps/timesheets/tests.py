from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate
from unittest.mock import patch

from apps.accounts.models import Membership, User, WorkerEngagement, WorkerProfile
from apps.tenants.models import Tenant
from apps.timesheets.models import Timesheet
from apps.timesheets.services import TimesheetService, TimesheetTransitionError, TimesheetValidationError
from apps.timesheets.views import (
    TenantTimesheetApproveView,
    TenantTimesheetListView,
    TenantTimesheetRejectView,
    WorkerTimesheetDetailView,
    WorkerTimesheetListCreateView,
    WorkerTimesheetSubmitView,
)


class TimesheetApiTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = self._tenant("tenant_one", "NorthBridge")
        self.worker_user = User.objects.create_user(
            username="worker@example.com",
            email="worker@example.com",
            password="WorkerPassword123!",
            first_name="Jordan",
            last_name="Reyes",
        )
        self.worker_profile = WorkerProfile.objects.create(
            user=self.worker_user,
            preferred_name="Jordan Reyes",
        )
        self.engagement = WorkerEngagement.objects.create(
            worker_profile=self.worker_profile,
            tenant=self.tenant,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-001",
            supplier_id=10,
            supplier_name="Apex Talent Partners",
            client_name="NorthBridge",
            role_name="Software Engineer",
            status=WorkerEngagement.STATUS_ACTIVE,
        )
        self.load_work_order_patch = patch.object(TimesheetService, "_load_tenant_work_order", return_value=None)
        self.load_work_order_patch.start()
        self.addCleanup(self.load_work_order_patch.stop)
        self.admin_user = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="AdminPassword123!",
        )
        self.admin_membership = Membership.objects.create(
            user=self.admin_user,
            tenant=self.tenant,
            role=Membership.ROLE_ADMIN,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )

    def _tenant(self, schema_name, name):
        tenant = Tenant(schema_name=schema_name, name=name)
        tenant.auto_create_schema = False
        tenant.save()
        return tenant

    def _request(self, method, path, user, data=None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.tenant
        request.session = {}
        force_authenticate(request, user=user)
        return request

    def _payload(self, period_start=date(2026, 8, 24), period_end=date(2026, 8, 30)):
        second_line_date = period_start + timedelta(days=1)
        return {
            "worker_engagement_id": self.engagement.id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "comment": "Weekly delivery work.",
            "lines": [
                {
                    "line_date": period_start.isoformat(),
                    "task_name": "Client workshops",
                    "hours": "8.00",
                },
                {
                    "line_date": second_line_date.isoformat(),
                    "task_name": "Design documentation",
                    "hours": "7.50",
                },
            ],
        }

    def _create_timesheet(self, **overrides):
        period_start = date.fromisoformat(overrides.pop("period_start", date(2026, 8, 24).isoformat()))
        period_end = date.fromisoformat(overrides.pop("period_end", date(2026, 8, 30).isoformat()))
        payload = self._payload(period_start=period_start, period_end=period_end)
        payload.update(overrides)
        return TimesheetService.create_for_worker(
            worker_profile=self.worker_profile,
            user=self.worker_user,
            attrs={
                **payload,
                "period_start": period_start,
                "period_end": period_end,
                "lines": [
                    {
                        **line,
                        "line_date": date.fromisoformat(line["line_date"]),
                        "hours": Decimal(line["hours"]),
                    }
                    for line in payload["lines"]
                ],
            },
        )

    def test_worker_can_list_create_update_and_submit_own_timesheet(self):
        response = WorkerTimesheetListCreateView.as_view()(
            self._request("post", "/api/worker/timesheets", self.worker_user, self._payload())
        )

        self.assertEqual(status.HTTP_201_CREATED, response.status_code)
        timesheet_id = response.data["id"]
        self.assertEqual(Timesheet.STATUS_DRAFT, response.data["status"])
        self.assertEqual("15.50", response.data["total_hours"])
        self.assertEqual("WO-001", response.data["work_order_number"])
        self.assertEqual("WO-001", response.data["lines"][0]["cost_center_code"])

        list_response = WorkerTimesheetListCreateView.as_view()(
            self._request("get", "/api/worker/timesheets", self.worker_user)
        )
        self.assertEqual(status.HTTP_200_OK, list_response.status_code)
        self.assertEqual(1, list_response.data["pagination"]["total_count"])

        patch_response = WorkerTimesheetDetailView.as_view()(
            self._request(
                "patch",
                f"/api/worker/timesheets/{timesheet_id}",
                self.worker_user,
                {"comment": "Updated context."},
            ),
            timesheet_id=timesheet_id,
        )
        self.assertEqual(status.HTTP_200_OK, patch_response.status_code)
        self.assertEqual("Updated context.", patch_response.data["comment"])

        submit_response = WorkerTimesheetSubmitView.as_view()(
            self._request("post", f"/api/worker/timesheets/{timesheet_id}/submit", self.worker_user),
            timesheet_id=timesheet_id,
        )
        self.assertEqual(status.HTTP_200_OK, submit_response.status_code)
        self.assertEqual(Timesheet.STATUS_SUBMITTED, submit_response.data["status"])
        self.assertIsNotNone(submit_response.data["submitted_at"])

    def test_worker_cannot_access_another_workers_timesheet(self):
        other_user = User.objects.create_user(username="other@example.com", email="other@example.com")
        other_profile = WorkerProfile.objects.create(user=other_user)
        other_engagement = WorkerEngagement.objects.create(
            worker_profile=other_profile,
            tenant=self.tenant,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-002",
            status=WorkerEngagement.STATUS_ACTIVE,
        )
        timesheet = Timesheet.objects.create(
            tenant=self.tenant,
            worker_profile=other_profile,
            worker_engagement=other_engagement,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-002",
            period_start=date(2026, 8, 24),
            period_end=date(2026, 8, 30),
        )

        response = WorkerTimesheetDetailView.as_view()(
            self._request("get", f"/api/worker/timesheets/{timesheet.id}", self.worker_user),
            timesheet_id=timesheet.id,
        )

        self.assertEqual(status.HTTP_404_NOT_FOUND, response.status_code)

    def test_worker_cannot_create_timesheet_for_inactive_engagement(self):
        inactive_engagement = WorkerEngagement.objects.create(
            worker_profile=self.worker_profile,
            tenant=self.tenant,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-INACTIVE",
            status=WorkerEngagement.STATUS_ENDED,
        )
        payload = self._payload()
        payload["worker_engagement_id"] = inactive_engagement.id

        response = WorkerTimesheetListCreateView.as_view()(
            self._request("post", "/api/worker/timesheets", self.worker_user, payload)
        )

        self.assertEqual(status.HTTP_400_BAD_REQUEST, response.status_code)
        self.assertIn("worker_engagement_id", response.data["errors"])

    def test_non_worker_cannot_access_worker_timesheet_api(self):
        response = WorkerTimesheetListCreateView.as_view()(
            self._request("get", "/api/worker/timesheets", self.admin_user)
        )

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)

    def test_tenant_admin_can_list_and_approve_or_reject_timesheets(self):
        first = self._create_timesheet()
        TimesheetService.submit_for_worker(
            timesheet=first,
            worker_profile=self.worker_profile,
            user=self.worker_user,
        )
        second = self._create_timesheet(
            period_start=date(2026, 8, 17).isoformat(),
            period_end=date(2026, 8, 23).isoformat(),
        )
        TimesheetService.submit_for_worker(
            timesheet=second,
            worker_profile=self.worker_profile,
            user=self.worker_user,
        )

        list_response = TenantTimesheetListView.as_view()(
            self._request("get", "/api/timesheets", self.admin_user)
        )
        self.assertEqual(status.HTTP_200_OK, list_response.status_code)
        self.assertEqual(2, list_response.data["pagination"]["total_count"])

        approve_response = TenantTimesheetApproveView.as_view()(
            self._request("post", f"/api/timesheets/{first.id}/approve", self.admin_user),
            timesheet_id=first.id,
        )
        self.assertEqual(status.HTTP_200_OK, approve_response.status_code)
        self.assertEqual(Timesheet.STATUS_APPROVED, approve_response.data["status"])

        reject_response = TenantTimesheetRejectView.as_view()(
            self._request(
                "post",
                f"/api/timesheets/{second.id}/reject",
                self.admin_user,
                {"rejection_reason": "Please clarify Friday."},
            ),
            timesheet_id=second.id,
        )
        self.assertEqual(status.HTTP_200_OK, reject_response.status_code)
        self.assertEqual(Timesheet.STATUS_REJECTED, reject_response.data["status"])
        self.assertEqual("Please clarify Friday.", reject_response.data["rejection_reason"])

    def test_supplier_user_is_scoped_to_supplier_id(self):
        supplier_user = User.objects.create_user(username="supplier@example.com", email="supplier@example.com")
        Membership.objects.create(
            user=supplier_user,
            tenant=self.tenant,
            role=Membership.ROLE_SUPPLIER,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
            supplier_id=10,
        )
        visible = self._create_timesheet()
        other_engagement = WorkerEngagement.objects.create(
            worker_profile=self.worker_profile,
            tenant=self.tenant,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-003",
            supplier_id=99,
            status=WorkerEngagement.STATUS_ACTIVE,
        )
        Timesheet.objects.create(
            tenant=self.tenant,
            worker_profile=self.worker_profile,
            worker_engagement=other_engagement,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-003",
            period_start=date(2026, 8, 17),
            period_end=date(2026, 8, 23),
        )

        response = TenantTimesheetListView.as_view()(
            self._request("get", "/api/timesheets", supplier_user)
        )

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual(1, response.data["pagination"]["total_count"])
        self.assertEqual(visible.id, response.data["results"][0]["id"])

    def test_unique_period_constraint_blocks_second_non_voided_timesheet(self):
        self._create_timesheet()

        with self.assertRaises(IntegrityError), transaction.atomic():
            Timesheet.objects.create(
                tenant=self.tenant,
                worker_profile=self.worker_profile,
                worker_engagement=self.engagement,
                engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
                work_order_number="WO-001",
                period_start=date(2026, 8, 24),
                period_end=date(2026, 8, 30),
            )

        voided = Timesheet.objects.create(
            tenant=self.tenant,
            worker_profile=self.worker_profile,
            worker_engagement=self.engagement,
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            work_order_number="WO-001",
            period_start=date(2026, 8, 24),
            period_end=date(2026, 8, 30),
            status=Timesheet.STATUS_VOIDED,
        )
        self.assertEqual(Timesheet.STATUS_VOIDED, voided.status)

    def test_submitted_timesheet_cannot_be_edited(self):
        timesheet = self._create_timesheet()
        TimesheetService.submit_for_worker(
            timesheet=timesheet,
            worker_profile=self.worker_profile,
            user=self.worker_user,
        )

        with self.assertRaises(TimesheetTransitionError):
            TimesheetService.update_worker_draft(
                timesheet=timesheet,
                worker_profile=self.worker_profile,
                user=self.worker_user,
                attrs={"comment": "Too late."},
            )

    def test_line_date_must_fall_within_period(self):
        payload = self._payload()
        payload["lines"][0]["line_date"] = "2026-09-01"

        with self.assertRaises(TimesheetValidationError):
            TimesheetService.create_for_worker(
                worker_profile=self.worker_profile,
                user=self.worker_user,
                attrs={
                    **payload,
                    "period_start": date.fromisoformat(payload["period_start"]),
                    "period_end": date.fromisoformat(payload["period_end"]),
                    "lines": [
                        {
                            **line,
                            "line_date": date.fromisoformat(line["line_date"]),
                            "hours": Decimal(line["hours"]),
                        }
                        for line in payload["lines"]
                    ],
                },
            )
