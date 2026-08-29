from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.api.session import SessionStatusView
from apps.accounts.api.worker import WorkerContextView
from apps.accounts.models import Membership, WorkerEngagement, WorkerProfile
from apps.accounts.profile import (
    DEFAULT_APP_HOME_PATH,
    NON_WORKER_TENANT_ROLES,
    PROFILE_INTERNAL,
    PROFILE_SUPPLIER,
    PROFILE_WORKER,
    SESSION_WORKER_ENGAGEMENT_ID_KEY,
    WORKER_HOME_PATH,
    build_membership_metadata,
    build_worker_profile_metadata,
    profile_type_for_membership,
    resolve_frontend_path_for_membership,
    resolve_frontend_path_for_profile,
    serialize_worker_engagement,
)
from apps.common.permissions import HasRole, IsWorkerProfile


class FakeEngagementQuery:
    def __init__(self, items):
        self.items = items

    def select_related(self, *_args):
        return self

    def __iter__(self):
        return iter(self.items)


class FakeEngagementManager:
    def __init__(self, items):
        self.items = items

    def filter(self, **_kwargs):
        return FakeEngagementQuery(self.items)


class WorkerProfileContractTests(SimpleTestCase):
    def test_profile_type_distinguishes_supplier_and_internal_memberships(self):
        self.assertEqual(
            PROFILE_SUPPLIER,
            profile_type_for_membership(SimpleNamespace(role=Membership.ROLE_SUPPLIER)),
        )
        self.assertEqual(
            PROFILE_INTERNAL,
            profile_type_for_membership(SimpleNamespace(role=Membership.ROLE_ADMIN)),
        )
        self.assertEqual(
            PROFILE_INTERNAL,
            profile_type_for_membership(SimpleNamespace(role=Membership.ROLE_BUSINESS)),
        )

    def test_worker_profile_metadata_exposes_home_and_flags(self):
        metadata = build_worker_profile_metadata()

        self.assertEqual(PROFILE_WORKER, metadata["type"])
        self.assertTrue(metadata["is_worker"])
        self.assertFalse(metadata["is_internal"])
        self.assertEqual(WORKER_HOME_PATH, metadata["default_home"])
        self.assertEqual([WORKER_HOME_PATH], metadata["allowed_frontend_paths"])

    def test_membership_metadata_preserves_non_worker_defaults(self):
        membership = SimpleNamespace(role=Membership.ROLE_BUSINESS, tenant_id=42)

        metadata = build_membership_metadata(membership)

        self.assertEqual(PROFILE_INTERNAL, metadata["profile_type"])
        self.assertFalse(metadata["is_worker"])
        self.assertTrue(metadata["is_internal"])
        self.assertEqual(DEFAULT_APP_HOME_PATH, metadata["default_home"])
        self.assertNotIn("allowed_frontend_paths", metadata)

    def test_frontend_redirect_resolution_keeps_workers_on_timesheet(self):
        self.assertEqual(
            WORKER_HOME_PATH,
            resolve_frontend_path_for_profile(PROFILE_WORKER, None),
        )
        self.assertEqual(
            WORKER_HOME_PATH,
            resolve_frontend_path_for_profile(PROFILE_WORKER, "/home"),
        )
        self.assertEqual(
            WORKER_HOME_PATH,
            resolve_frontend_path_for_profile(PROFILE_WORKER, WORKER_HOME_PATH),
        )

    def test_frontend_redirect_resolution_keeps_non_workers_out_of_worker_home(self):
        membership = SimpleNamespace(role=Membership.ROLE_ADMIN, tenant_id=42)

        self.assertEqual(
            DEFAULT_APP_HOME_PATH,
            resolve_frontend_path_for_membership(membership, WORKER_HOME_PATH),
        )
        self.assertEqual(
            "/home/approvals",
            resolve_frontend_path_for_membership(membership, "/home/approvals"),
        )
        self.assertEqual(
            DEFAULT_APP_HOME_PATH,
            resolve_frontend_path_for_membership(membership, "https://example.com"),
        )

    def test_worker_engagement_serialization_uses_tenant_scoped_reference(self):
        engagement = SimpleNamespace(
            id=12,
            tenant_id=3,
            tenant=SimpleNamespace(name="NorthBridge"),
            client_name="",
            engagement_type=WorkerEngagement.TYPE_WORK_ORDER,
            status=WorkerEngagement.STATUS_ACTIVE,
            work_order_id=99,
            work_order_number="WO-2026-00099",
            sow_id=None,
            sow_number="",
            supplier_id=7,
            supplier_name="Acorena",
            role_name="Data Analyst",
            start_date=None,
            end_date=None,
        )

        payload = serialize_worker_engagement(engagement)

        self.assertEqual(3, payload["tenant_id"])
        self.assertEqual("NorthBridge", payload["tenant_name"])
        self.assertEqual("WO-2026-00099", payload["work_order_number"])
        self.assertEqual(WORKER_HOME_PATH, payload["home_path"])


class WorkerAuthorizationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = SimpleNamespace(id=42, schema_name="tenant")
        self.user = SimpleNamespace(
            id=7,
            email="worker@example.com",
            first_name="Jordan",
            last_name="Reyes",
            is_authenticated=True,
        )

    def request(self, path):
        request = self.factory.get(path)
        request.tenant = self.tenant
        request.user = self.user
        request.session = {}
        force_authenticate(request, user=self.user)
        return request

    def post_request(self, path, data):
        request = self.factory.post(path, data, format="json")
        request.tenant = self.tenant
        request.user = self.user
        request.session = {}
        force_authenticate(request, user=self.user)
        return request

    def test_session_status_includes_worker_profile_and_engagements_without_membership(self):
        request = self.request("/api/session")
        worker_profile = SimpleNamespace(id=9, status=WorkerProfile.STATUS_ACTIVE)
        worker_payload = {
            "profile_id": 9,
            "status": WorkerProfile.STATUS_ACTIVE,
            "active_tenant_id": 42,
            "active_engagement_id": 11,
            "active_engagement": {"id": 11, "tenant_id": 42},
            "engagements": [{"id": 11, "tenant_id": 42}],
        }

        with (
            patch("apps.accounts.api.session.get_active_tenant_membership", return_value=None),
            patch("apps.accounts.api.session.get_active_worker_profile", return_value=worker_profile),
            patch("apps.accounts.api.session.build_worker_session_metadata", return_value=worker_payload),
        ):
            response = SessionStatusView.as_view()(request)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual(PROFILE_WORKER, response.data["profile"]["type"])
        self.assertEqual(WORKER_HOME_PATH, response.data["profile"]["default_home"])
        self.assertEqual(11, response.data["worker"]["active_engagement_id"])
        self.assertNotIn("membership", response.data)

    def test_worker_permission_allows_active_worker_engagement_only(self):
        request = self.request("/api/worker/context")

        with patch("apps.common.permissions.user_has_active_worker_engagement", return_value=True):
            self.assertTrue(IsWorkerProfile().has_permission(request, None))

        with patch("apps.common.permissions.user_has_active_worker_engagement", return_value=False):
            self.assertFalse(IsWorkerProfile().has_permission(request, None))

    def test_membership_role_gate_denies_global_worker_without_tenant_membership(self):
        request = self.request("/api/work-orders")
        view = SimpleNamespace(required_roles=NON_WORKER_TENANT_ROLES)

        with patch("apps.common.permissions.get_active_tenant_membership", return_value=None):
            self.assertFalse(HasRole().has_permission(request, view))

    def test_worker_context_route_denies_non_workers(self):
        request = self.request("/api/worker/context")

        with patch("apps.common.permissions.user_has_active_worker_engagement", return_value=False):
            response = WorkerContextView.as_view()(request)

        self.assertEqual(status.HTTP_403_FORBIDDEN, response.status_code)

    def test_worker_context_route_allows_workers(self):
        request = self.request("/api/worker/context")
        worker_profile = SimpleNamespace(id=9, status=WorkerProfile.STATUS_ACTIVE)
        worker_payload = {
            "profile_id": 9,
            "status": WorkerProfile.STATUS_ACTIVE,
            "active_tenant_id": 42,
            "active_engagement_id": 11,
            "active_engagement": {"id": 11, "tenant_id": 42},
            "engagements": [{"id": 11, "tenant_id": 42}],
        }

        with (
            patch("apps.common.permissions.user_has_active_worker_engagement", return_value=True),
            patch("apps.accounts.api.worker.get_active_worker_profile", return_value=worker_profile),
            patch("apps.accounts.api.worker.build_worker_session_metadata", return_value=worker_payload),
        ):
            response = WorkerContextView.as_view()(request)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual(PROFILE_WORKER, response.data["profile"]["type"])
        self.assertEqual(11, response.data["worker"]["active_engagement_id"])

    def test_worker_context_route_switches_active_engagement(self):
        request = self.post_request("/api/worker/context", {"engagement_id": 12})
        engagement = SimpleNamespace(id=12)
        worker_profile = SimpleNamespace(
            id=9,
            status=WorkerProfile.STATUS_ACTIVE,
            engagements=FakeEngagementManager([engagement]),
        )
        worker_payload = {
            "profile_id": 9,
            "status": WorkerProfile.STATUS_ACTIVE,
            "active_tenant_id": 43,
            "active_engagement_id": 12,
            "active_engagement": {"id": 12, "tenant_id": 43},
            "engagements": [{"id": 12, "tenant_id": 43}],
        }

        with (
            patch("apps.common.permissions.user_has_active_worker_engagement", return_value=True),
            patch("apps.accounts.api.worker.get_active_worker_profile", return_value=worker_profile),
            patch("apps.accounts.api.worker.build_worker_session_metadata", return_value=worker_payload),
        ):
            response = WorkerContextView.as_view()(request)

        self.assertEqual(status.HTTP_200_OK, response.status_code)
        self.assertEqual(12, request.session[SESSION_WORKER_ENGAGEMENT_ID_KEY])
        self.assertEqual(12, response.data["worker"]["active_engagement_id"])
