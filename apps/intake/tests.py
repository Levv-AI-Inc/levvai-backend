from datetime import timedelta

from django.utils import timezone
from django_tenants.test.cases import FastTenantTestCase
from django_tenants.test.client import TenantClient

from apps.accounts.models import Membership, User
from apps.intake.models import (
    IntakeQualification,
    IntakeRequest,
    IntakeSelectedCandidate,
)
from apps.masterdata.models import RoleDefinition, Supplier
from apps.workers.models import Engagement
from apps.workorders.models import WorkOrder


class CandidateDirectoryApiTests(FastTenantTestCase):
    ADMIN_PASSWORD = "CandidateAdmin!2026"
    SUPPLIER_PASSWORD = "CandidateSupplier!2026"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Candidate Directory Test Tenant"

    def setUp(self):
        self.client = TenantClient(self.tenant)
        self.admin = User.objects.create_user(
            username="candidate-admin@example.com",
            email="candidate-admin@example.com",
            password=self.ADMIN_PASSWORD,
            first_name="Alex",
            last_name="Morgan",
        )
        Membership.objects.create(
            user=self.admin,
            tenant=self.tenant,
            role=Membership.ROLE_ADMIN,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )
        self.supplier = Supplier.objects.create(
            supplier_code="SUP-CANDIDATE-A",
            name="Candidate Supplier A",
            email="supplier-a@example.com",
        )
        self.other_supplier = Supplier.objects.create(
            supplier_code="SUP-CANDIDATE-B",
            name="Candidate Supplier B",
            email="supplier-b@example.com",
        )
        self.role = RoleDefinition.objects.create(
            code="candidate-backend-engineer",
            name="Senior Backend Engineer",
            country="CA",
        )
        self.intake = IntakeRequest.objects.create(
            tenant_id=self.tenant.id,
            created_by=self.admin,
            status=IntakeRequest.STATUS_APPROVED,
            approval_status="approved",
            supplier=self.supplier,
            role_definition=self.role,
            title="Backend Platform Engineer",
            city="Toronto",
            state_province="Ontario",
            country="CA",
        )
        IntakeQualification.objects.create(
            intake=self.intake,
            sequence=1,
            name="Distributed systems",
            tags=["Golang", "Kubernetes"],
        )
        self.candidate = IntakeSelectedCandidate.objects.create(
            intake=self.intake,
            supplier=self.supplier,
            submitted_by=self.admin,
            full_name="James Carter",
            email="james.carter@example.com",
            proposed_rate="108.00",
            currency="CAD",
            notes="Strong technical screen.",
        )
        self.other_candidate = IntakeSelectedCandidate.objects.create(
            intake=self.intake,
            supplier=self.other_supplier,
            submitted_by=self.admin,
            full_name="Priya Shah",
            email="priya.shah@example.com",
            status=IntakeSelectedCandidate.STATUS_REVIEWED,
        )
        IntakeSelectedCandidate.objects.filter(pk=self.candidate.pk).update(
            updated_at=timezone.now() - timedelta(days=9)
        )
        self.candidate.refresh_from_db()
    def test_directory_returns_candidate_context_filters_and_summary(self):
        WorkOrder.objects.create(
            tenant_id=self.tenant.id,
            intake=self.intake,
            selected_candidate=self.candidate,
            supplier=self.supplier,
            work_order_number="WO-2026-00001",
            worker_full_name=self.candidate.full_name,
            worker_email=self.candidate.email,
            status=WorkOrder.STATUS_SUBMITTED,
        )
        login_response = self.client.post(
            "/auth/password/login-user",
            {
                "email": self.admin.email,
                "password": self.ADMIN_PASSWORD,
            },
        )
        self.assertEqual(200, login_response.status_code, login_response.content)

        response = self.client.get(
            "/api/candidates",
            {
                "status": "submitted",
                "search": "James",
                "page_size": 1,
            },
        )

        self.assertEqual(200, response.status_code, response.content)
        payload = response.json()
        self.assertTrue(payload["permissions"]["can_decide"])
        self.assertEqual(2, payload["summary"]["total_count"])
        self.assertEqual(1, payload["summary"]["stalled_count"])
        self.assertEqual(
            {"reviewed": 1, "submitted": 1},
            payload["summary"]["status_counts"],
        )
        self.assertEqual(1, payload["pagination"]["total_count"])
        self.assertEqual(1, len(payload["results"]))

        candidate = payload["results"][0]
        self.assertEqual(self.candidate.id, candidate["id"])
        self.assertEqual("Senior Backend Engineer", candidate["role_name"])
        self.assertEqual("Candidate Supplier A", candidate["supplier_name"])
        self.assertEqual("Alex Morgan", candidate["hiring_manager_name"])
        self.assertEqual("Toronto, Ontario, CA", candidate["location"])
        self.assertEqual(["Golang", "Kubernetes"], candidate["skills"])
        self.assertEqual("WO-2026-00001", candidate["work_order_number"])
        self.assertEqual("submitted", candidate["work_order_status"])
        self.assertGreaterEqual(candidate["days_in_stage"], 9)

    def test_supplier_only_sees_candidates_from_their_supplier(self):
        supplier_user = User.objects.create_user(
            username="candidate-supplier@example.com",
            email="candidate-supplier@example.com",
            password=self.SUPPLIER_PASSWORD,
        )
        Membership.objects.create(
            user=supplier_user,
            tenant=self.tenant,
            role=Membership.ROLE_SUPPLIER,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
            supplier_id=self.supplier.id,
        )
        login_response = self.client.post(
            "/auth/password/login",
            {
                "email": supplier_user.email,
                "password": self.SUPPLIER_PASSWORD,
            },
        )
        self.assertEqual(200, login_response.status_code, login_response.content)

        response = self.client.get("/api/candidates")

        self.assertEqual(200, response.status_code, response.content)
        payload = response.json()
        self.assertFalse(payload["permissions"]["can_decide"])
        self.assertEqual(1, payload["summary"]["total_count"])
        self.assertEqual(
            [self.candidate.id],
            [candidate["id"] for candidate in payload["results"]],
        )

        decision_response = self.client.patch(
            f"/api/candidates/{self.candidate.id}",
            {"status": IntakeSelectedCandidate.STATUS_REVIEWED},
            content_type="application/json",
        )
        self.assertEqual(403, decision_response.status_code)

        work_order_response = self.client.post(
            "/api/work-orders",
            {
                "intake": self.intake.id,
                "selected_candidate": self.candidate.id,
            },
            content_type="application/json",
        )
        self.assertEqual(403, work_order_response.status_code)

        submission_response = self.client.post(
            f"/api/intake/{self.intake.id}/selected-candidates",
            {
                "full_name": "Morgan Lee",
                "email": "morgan.lee@example.com",
                "proposed_rate": "102.00",
                "currency": "CAD",
            },
            content_type="application/json",
        )
        self.assertEqual(
            201,
            submission_response.status_code,
            submission_response.content,
        )
        self.assertEqual(
            IntakeSelectedCandidate.STATUS_SUBMITTED,
            submission_response.json()["status"],
        )
        self.assertFalse(WorkOrder.objects.exists())

    def test_buyer_reviews_then_selects_exactly_one_candidate(self):
        login_response = self.client.post(
            "/auth/password/login-user",
            {
                "email": self.admin.email,
                "password": self.ADMIN_PASSWORD,
            },
        )
        self.assertEqual(200, login_response.status_code, login_response.content)

        skipped_review_response = self.client.patch(
            f"/api/candidates/{self.candidate.id}",
            {"status": IntakeSelectedCandidate.STATUS_ACCEPTED},
            content_type="application/json",
        )
        self.assertEqual(409, skipped_review_response.status_code)

        review_response = self.client.patch(
            f"/api/candidates/{self.candidate.id}",
            {"status": IntakeSelectedCandidate.STATUS_REVIEWED},
            content_type="application/json",
        )
        self.assertEqual(200, review_response.status_code, review_response.content)
        self.assertEqual(
            IntakeSelectedCandidate.STATUS_REVIEWED,
            review_response.json()["status"],
        )

        select_response = self.client.patch(
            f"/api/candidates/{self.candidate.id}",
            {"status": IntakeSelectedCandidate.STATUS_ACCEPTED},
            content_type="application/json",
        )
        self.assertEqual(200, select_response.status_code, select_response.content)
        self.assertEqual(
            IntakeSelectedCandidate.STATUS_ACCEPTED,
            select_response.json()["status"],
        )

        replace_response = self.client.patch(
            f"/api/candidates/{self.other_candidate.id}",
            {"status": IntakeSelectedCandidate.STATUS_ACCEPTED},
            content_type="application/json",
        )
        self.assertEqual(200, replace_response.status_code, replace_response.content)
        self.candidate.refresh_from_db()
        self.other_candidate.refresh_from_db()
        self.assertEqual(
            IntakeSelectedCandidate.STATUS_REVIEWED,
            self.candidate.status,
        )
        self.assertEqual(
            IntakeSelectedCandidate.STATUS_ACCEPTED,
            self.other_candidate.status,
        )

    def test_work_order_creation_rejects_unselected_candidate(self):
        login_response = self.client.post(
            "/auth/password/login-user",
            {
                "email": self.admin.email,
                "password": self.ADMIN_PASSWORD,
            },
        )
        self.assertEqual(200, login_response.status_code, login_response.content)

        response = self.client.post(
            "/api/work-orders",
            {
                "intake": self.intake.id,
                "selected_candidate": self.candidate.id,
            },
            content_type="application/json",
        )

        self.assertEqual(400, response.status_code, response.content)
        self.assertEqual("not_selected", response.json()["errors"][0]["code"])
        self.assertFalse(WorkOrder.objects.exists())

    def test_current_approver_receives_work_order_decision_permissions(self):
        self.candidate.status = IntakeSelectedCandidate.STATUS_ACCEPTED
        self.candidate.save(update_fields=["status", "updated_at"])
        work_order = WorkOrder.objects.create(
            tenant_id=self.tenant.id,
            intake=self.intake,
            selected_candidate=self.candidate,
            supplier=self.supplier,
            role_definition=self.role,
            work_order_number="WO-2026-APPROVAL",
            worker_full_name=self.candidate.full_name,
            worker_email=self.candidate.email,
            status=WorkOrder.STATUS_SUBMITTED,
            approval_status=WorkOrder.APPROVAL_PROCESSING,
            approval_chain_snapshot={
                "current_step_sequence": 1,
                "approvals_remaining": 1,
                "resolved_steps": [
                    {
                        "sequence": 1,
                        "status": "pending",
                        "approver_id": self.admin.id,
                        "approver_name": "Alex Morgan",
                    }
                ],
            },
        )
        login_response = self.client.post(
            "/auth/password/login-user",
            {
                "email": self.admin.email,
                "password": self.ADMIN_PASSWORD,
            },
        )
        self.assertEqual(200, login_response.status_code, login_response.content)

        response = self.client.get(f"/api/work-orders/{work_order.id}")

        self.assertEqual(200, response.status_code, response.content)
        permissions = response.json()["permissions"]
        self.assertTrue(permissions["can_approve"])
        self.assertTrue(permissions["can_reject"])
        self.assertFalse(permissions["can_respond_to_work_order"])

        approval_response = self.client.post(
            f"/api/work-orders/{work_order.id}/approve",
            {},
            content_type="application/json",
        )

        self.assertEqual(
            200,
            approval_response.status_code,
            approval_response.content,
        )
        approved = approval_response.json()
        self.assertEqual(WorkOrder.STATUS_APPROVED, approved["status"])
        self.assertEqual(
            WorkOrder.SUPPLIER_ACCEPTANCE_PENDING,
            approved["supplier_acceptance_status"],
        )
        self.assertEqual(0, Engagement.objects.count())
