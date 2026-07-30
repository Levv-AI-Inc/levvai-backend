from datetime import date, timedelta

from django.core import mail
from django.test import override_settings
from django_tenants.test.cases import FastTenantTestCase
from django_tenants.test.client import TenantClient

from apps.accounts.models import Membership, User
from apps.audit.models import AuditEvent
from apps.masterdata.models import RoleDefinition, Supplier
from apps.policies.models import (
    WorkerLifecycleWorkflow,
    WorkflowBlock,
    WorkflowBlockRequirement,
    WorkflowPolicyScope,
    WorkflowRequirement,
)
from apps.workers.models import (
    Engagement,
    LifecycleRun,
    Worker,
    WorkerEngagement,
    WorkerInvite,
)
from apps.workorders.models import WorkOrder


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class WorkerLifecycleIntegrationTests(FastTenantTestCase):
    ADMIN_PASSWORD = "AdminAccess!2026"
    SUPPLIER_PASSWORD = "SupplierAccess!2026"
    WORKER_PASSWORD = "WorkerAccess!2026"

    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Lifecycle Test Tenant"

    def setUp(self):
        self.client = TenantClient(self.tenant)
        self.admin_client = TenantClient(self.tenant)
        self.worker_client = TenantClient(self.tenant)

        self.admin = User.objects.create_user(
            username="lifecycle-admin@example.com",
            email="lifecycle-admin@example.com",
            password=self.ADMIN_PASSWORD,
            first_name="Lifecycle",
            last_name="Admin",
        )
        Membership.objects.create(
            user=self.admin,
            tenant=self.tenant,
            role=Membership.ROLE_ADMIN,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )

        self.supplier = Supplier.objects.create(
            supplier_code="SUP-LIFECYCLE",
            name="Lifecycle Supplier",
            email="supplier@example.com",
        )
        self.supplier_user = User.objects.create_user(
            username="supplier-user@example.com",
            email="supplier-user@example.com",
            password=self.SUPPLIER_PASSWORD,
            first_name="Supplier",
            last_name="User",
        )
        Membership.objects.create(
            user=self.supplier_user,
            tenant=self.tenant,
            role=Membership.ROLE_SUPPLIER,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
            supplier_id=self.supplier.id,
        )
        self.role = RoleDefinition.objects.create(
            code="senior-react-developer-ca",
            name="Senior React Developer",
            country="CA",
            region="Ontario",
            city="Toronto",
        )

        self.generic_workflow = WorkerLifecycleWorkflow.objects.create(
            tenant_id=self.tenant.id,
            name="Generic Published Onboarding",
            workflow_type=WorkerLifecycleWorkflow.TYPE_ONBOARDING,
            status=WorkerLifecycleWorkflow.STATUS_PUBLISHED,
            is_active=True,
            version=1,
        )
        self.scoped_workflow = self._create_scoped_workflow()

    def _create_scoped_workflow(self):
        workflow = WorkerLifecycleWorkflow.objects.create(
            tenant_id=self.tenant.id,
            name="Scoped Draft Onboarding",
            workflow_type=WorkerLifecycleWorkflow.TYPE_ONBOARDING,
            status=WorkerLifecycleWorkflow.STATUS_DRAFT,
            is_active=True,
            version=3,
            dependencies=[
                {
                    "from_block_key": "__start__",
                    "to_block_key": "identity",
                },
                {
                    "from_block_key": "identity",
                    "to_block_key": "provision",
                },
                {
                    "from_block_key": "provision",
                    "to_block_key": "__end__",
                },
            ],
        )
        WorkflowPolicyScope.objects.create(
            workflow=workflow,
            worker_type="contingent",
        )
        requirement = WorkflowRequirement.objects.create(
            tenant_id=self.tenant.id,
            code="government_id",
            name="Government ID Verification",
            default_owner=WorkflowRequirement.OWNER_WORKER,
        )
        identity = WorkflowBlock.objects.create(
            workflow=workflow,
            sequence=1,
            client_key="identity",
            block_type=WorkflowBlock.TYPE_REQUIREMENT,
            name="Identity & Eligibility",
            gate_type=WorkflowBlock.GATE_HARD,
            config={
                "completion_rule": "ALL",
                "accountable_owner": "worker",
            },
            layout={"level": 0, "position": 0},
        )
        WorkflowBlockRequirement.objects.create(
            block=identity,
            requirement=requirement,
            sequence=1,
            name=requirement.name,
            owner=WorkflowRequirement.OWNER_WORKER,
            config={
                "unwind": {
                    "action": "Purge Government ID",
                    "mode": "automated",
                }
            },
        )
        WorkflowBlock.objects.create(
            workflow=workflow,
            sequence=2,
            client_key="provision",
            block_type=WorkflowBlock.TYPE_SYSTEM,
            name="Workday Provisioning",
            gate_type=WorkflowBlock.GATE_HARD,
            integration_type=WorkflowBlock.INTEGRATION_API_CALL,
            config={
                "accountable_owner": "system",
                "system_integration": "Workday",
                "system_unwind": {
                    "action": "Deactivate Workday Record",
                    "mode": "automated",
                    "reconcile": True,
                },
            },
            layout={"level": 1, "position": 0},
        )
        return workflow

    def _create_work_order(
        self,
        *,
        worker_name="John Smith",
        worker_email="john.smith@example.com",
    ):
        return WorkOrder.objects.create(
            tenant_id=self.tenant.id,
            work_order_number=(
                f"WO-TEST-{WorkOrder.objects.count() + 1:03d}"
            ),
            supplier=self.supplier,
            role_definition=self.role,
            worker_full_name=worker_name,
            worker_email=worker_email,
            status=WorkOrder.STATUS_APPROVED,
            approval_status=WorkOrder.APPROVAL_APPROVED,
            supplier_acceptance_status=(
                WorkOrder.SUPPLIER_ACCEPTANCE_PENDING
            ),
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=180),
            bill_rate="125.00",
            pay_rate="95.00",
            currency="CAD",
            hours_per_week="40.00",
            work_location_label="Toronto",
            source_snapshot={
                "effective_values": {
                    "hiring_manager_name": "Alex Morgan",
                }
            },
            created_by=self.admin,
        )

    def _login_supplier(self):
        response = self.client.post(
            "/auth/password/login",
            {
                "email": self.supplier_user.email,
                "password": self.SUPPLIER_PASSWORD,
            },
        )
        self.assertEqual(200, response.status_code, response.content)

    def _login_admin(self):
        response = self.admin_client.post(
            "/auth/password/login-user",
            {
                "email": self.admin.email,
                "password": self.ADMIN_PASSWORD,
            },
        )
        self.assertEqual(200, response.status_code, response.content)

    def _accept_work_order(self, work_order):
        self._login_supplier()
        response = self.client.post(
            f"/api/work-orders/{work_order.id}/accept",
            {"supplier_response_notes": "Worker details confirmed."},
            content_type="application/json",
        )
        return response

    def _register_and_login_worker(self, acceptance_payload):
        invite = WorkerInvite.objects.get(
            worker_id=acceptance_payload["worker_id"],
            status=WorkerInvite.STATUS_PENDING,
        )
        response = self.worker_client.post(
            "/auth/password/register-worker",
            {
                "email": "john.smith@example.com",
                "password": self.WORKER_PASSWORD,
                "worker_invite_token": invite.token,
            },
            content_type="application/json",
        )
        self.assertEqual(201, response.status_code, response.content)
        self.assertEqual(
            (
                f"/workers/{acceptance_payload['worker_id']}/engagements/"
                "onboarding/workspace"
                f"?work_order={invite.work_order_id}"
            ),
            response.json()["next"],
        )

        response = self.worker_client.post(
            "/auth/password/login-user",
            {
                "email": "john.smith@example.com",
                "password": self.WORKER_PASSWORD,
            },
        )
        self.assertEqual(200, response.status_code, response.content)

    def _complete_onboarding_as_admin(self, *, worker_id, work_order_id):
        self._login_admin()
        detail_response = self.admin_client.get(
            (
                f"/api/workers/{worker_id}/lifecycle/onboarding"
                f"?work_order={work_order_id}"
            )
        )
        self.assertEqual(200, detail_response.status_code)
        detail = detail_response.json()
        for _ in range(8):
            if detail["run_status"] == LifecycleRun.STATUS_COMPLETE:
                return detail
            activity = next(
                activity
                for block in detail["blocks"]
                if block["status"] != "gated"
                for activity in block["activities"]
                if activity["status"] not in {"complete", "waived"}
            )
            detail_response = self.admin_client.post(
                (
                    f"/api/workers/{worker_id}/lifecycle/onboarding/"
                    f"activities/{activity['id']}"
                ),
                {"status": "complete"},
                content_type="application/json",
            )
            self.assertEqual(200, detail_response.status_code)
            detail = detail_response.json()
        self.fail("Onboarding did not complete within the expected transitions.")

    def test_new_worker_registration_onboarding_and_derived_offboarding(self):
        work_order = self._create_work_order()

        acceptance_response = self._accept_work_order(work_order)

        self.assertEqual(
            200,
            acceptance_response.status_code,
            acceptance_response.content,
        )
        acceptance = acceptance_response.json()
        self.assertTrue(acceptance["worker_is_new"])
        self.assertTrue(acceptance["registration_required"])
        self.assertEqual(
            self.scoped_workflow.id,
            acceptance["matched_workflow_id"],
        )
        self.assertEqual(1, len(mail.outbox))
        self.assertEqual(0, Engagement.objects.count())
        invite = WorkerInvite.objects.get(
            worker_id=acceptance["worker_id"],
            status=WorkerInvite.STATUS_PENDING,
        )
        self.assertIn(
            invite.token,
            mail.outbox[0].body,
        )

        self._register_and_login_worker(acceptance)
        worker_id = acceptance["worker_id"]
        detail_response = self.worker_client.get(
            f"/api/workers/{worker_id}/lifecycle/onboarding"
        )
        self.assertEqual(200, detail_response.status_code)
        detail = detail_response.json()
        self.assertEqual("Scoped Draft Onboarding", detail["workflow"]["name"])
        self.assertFalse(detail["permissions"]["can_manage_worker"])
        self.assertEqual("in_progress", detail["blocks"][0]["status"])
        self.assertEqual("gated", detail["blocks"][1]["status"])

        worker_activity = detail["blocks"][0]["activities"][0]
        self.assertTrue(worker_activity["can_update"])
        denied_response = self.worker_client.post(
            (
                f"/api/workers/{worker_id}/lifecycle/onboarding/"
                f"activities/{worker_activity['id']}"
            ),
            {"status": "waived"},
            content_type="application/json",
        )
        self.assertEqual(403, denied_response.status_code)
        completion_response = self.worker_client.post(
            (
                f"/api/workers/{worker_id}/lifecycle/onboarding/"
                f"activities/{worker_activity['id']}"
            ),
            {
                "status": "complete",
                "notes": "Identity evidence submitted.",
            },
            content_type="application/json",
        )
        self.assertEqual(200, completion_response.status_code)
        detail = completion_response.json()
        self.assertEqual("complete", detail["blocks"][0]["status"])
        self.assertEqual("in_progress", detail["blocks"][1]["status"])

        system_activity = detail["blocks"][1]["activities"][0]
        denied_response = self.worker_client.post(
            (
                f"/api/workers/{worker_id}/lifecycle/onboarding/"
                f"activities/{system_activity['id']}"
            ),
            {"status": "complete"},
            content_type="application/json",
        )
        self.assertEqual(403, denied_response.status_code)
        denied_response = self.worker_client.post(
            f"/api/workers/{worker_id}/offboarding/start",
        )
        self.assertEqual(403, denied_response.status_code)

        self._login_admin()
        completion_response = self.admin_client.post(
            (
                f"/api/workers/{worker_id}/lifecycle/onboarding/"
                f"activities/{system_activity['id']}"
            ),
            {"status": "complete"},
            content_type="application/json",
        )
        self.assertEqual(200, completion_response.status_code)
        self.assertEqual("complete", completion_response.json()["run_status"])

        worker = Worker.objects.get(pk=worker_id)
        assignment = WorkerEngagement.objects.get(
            pk=acceptance["worker_assignment_id"]
        )
        worker.refresh_from_db()
        assignment.refresh_from_db()
        work_order.refresh_from_db()
        self.assertEqual(Worker.STATUS_ACTIVE, worker.status)
        self.assertEqual(WorkerEngagement.STATUS_ACTIVE, assignment.status)
        self.assertEqual(work_order.id, assignment.work_order_id)
        self.assertIsNone(assignment.engagement_id)
        self.assertEqual(WorkOrder.STATUS_ACTIVE, work_order.status)

        offboarding_response = self.admin_client.post(
            f"/api/workers/{worker_id}/offboarding/start",
        )
        self.assertEqual(201, offboarding_response.status_code)
        offboarding = offboarding_response.json()
        self.assertTrue(offboarding["workflow"]["derived"])
        self.assertEqual(
            "Deactivate Workday Record",
            offboarding["blocks"][0]["name"],
        )

        for _ in range(4):
            if offboarding["run_status"] == LifecycleRun.STATUS_COMPLETE:
                break
            activity = next(
                activity
                for block in offboarding["blocks"]
                if block["status"] != "gated"
                for activity in block["activities"]
                if activity["status"] not in {"complete", "waived"}
            )
            offboarding_response = self.admin_client.post(
                (
                    f"/api/workers/{worker_id}/lifecycle/offboarding/"
                    f"activities/{activity['id']}"
                ),
                {"status": "complete"},
                content_type="application/json",
            )
            self.assertEqual(200, offboarding_response.status_code)
            offboarding = offboarding_response.json()

        self.assertEqual(LifecycleRun.STATUS_COMPLETE, offboarding["run_status"])
        worker.refresh_from_db()
        assignment.refresh_from_db()
        work_order.refresh_from_db()
        self.assertEqual(Worker.STATUS_OFFBOARDED, worker.status)
        self.assertEqual(WorkerEngagement.STATUS_COMPLETE, assignment.status)
        self.assertEqual(WorkOrder.STATUS_CLOSED, work_order.status)

    def test_registered_worker_is_reused_without_another_invite(self):
        first_work_order = self._create_work_order()
        first_response = self._accept_work_order(first_work_order)
        self.assertEqual(200, first_response.status_code)
        first_acceptance = first_response.json()
        self._register_and_login_worker(first_acceptance)

        second_work_order = self._create_work_order(
            worker_name="John Smith",
            worker_email="john.smith@example.com",
        )
        second_response = self.client.post(
            f"/api/work-orders/{second_work_order.id}/accept",
            {},
            content_type="application/json",
        )

        self.assertEqual(200, second_response.status_code, second_response.content)
        second_acceptance = second_response.json()
        self.assertFalse(second_acceptance["worker_is_new"])
        self.assertFalse(second_acceptance["registration_required"])
        self.assertEqual(
            first_acceptance["worker_id"],
            second_acceptance["worker_id"],
        )
        self.assertEqual(1, Worker.objects.count())
        self.assertEqual(0, Engagement.objects.count())
        self.assertEqual(1, len(mail.outbox))

        worker_id = first_acceptance["worker_id"]
        self._complete_onboarding_as_admin(
            worker_id=worker_id,
            work_order_id=first_work_order.id,
        )
        self._complete_onboarding_as_admin(
            worker_id=worker_id,
            work_order_id=second_work_order.id,
        )
        offboarding_response = self.admin_client.post(
            f"/api/workers/{worker_id}/offboarding/start",
            {"work_order_id": first_work_order.id},
            content_type="application/json",
        )
        self.assertEqual(201, offboarding_response.status_code)
        self.assertEqual(
            first_work_order.id,
            offboarding_response.json()["work_order_id"],
        )
        worker = Worker.objects.get(pk=worker_id)
        first_assignment = WorkerEngagement.objects.get(
            work_order=first_work_order
        )
        second_assignment = WorkerEngagement.objects.get(
            work_order=second_work_order
        )
        self.assertEqual(Worker.STATUS_ACTIVE, worker.status)
        self.assertEqual(
            WorkerEngagement.STATUS_OFFBOARDING,
            first_assignment.status,
        )
        self.assertEqual(
            WorkerEngagement.STATUS_ACTIVE,
            second_assignment.status,
        )

    def test_existing_worker_account_is_linked_without_registration(self):
        existing_user = User.objects.create_user(
            username="existing.worker@example.com",
            email="existing.worker@example.com",
            password=self.WORKER_PASSWORD,
            first_name="Existing",
            last_name="Worker",
        )
        Membership.objects.create(
            user=existing_user,
            tenant=self.tenant,
            role=Membership.ROLE_WORKER,
            status=Membership.STATUS_ACTIVE,
            is_active=True,
        )
        work_order = self._create_work_order(
            worker_name="Existing Worker",
            worker_email=existing_user.email,
        )

        response = self._accept_work_order(work_order)

        self.assertEqual(200, response.status_code, response.content)
        payload = response.json()
        self.assertFalse(payload["worker_is_new"])
        self.assertFalse(payload["registration_required"])
        worker = Worker.objects.get(pk=payload["worker_id"])
        self.assertEqual(existing_user.id, worker.user_id)
        self.assertFalse(WorkerInvite.objects.filter(worker=worker).exists())
        self.assertEqual(0, Engagement.objects.count())

    def test_acceptance_rolls_back_when_no_workflow_matches(self):
        self.generic_workflow.is_active = False
        self.generic_workflow.save(update_fields=["is_active"])
        self.scoped_workflow.is_active = False
        self.scoped_workflow.save(update_fields=["is_active"])
        work_order = self._create_work_order()

        response = self._accept_work_order(work_order)

        self.assertEqual(409, response.status_code)
        self.assertIn("No active onboarding workflow", response.json()["detail"])
        work_order.refresh_from_db()
        self.assertEqual(
            WorkOrder.SUPPLIER_ACCEPTANCE_PENDING,
            work_order.supplier_acceptance_status,
        )
        self.assertEqual(0, Engagement.objects.count())
        self.assertEqual(0, Worker.objects.count())
        self.assertEqual(0, WorkerEngagement.objects.count())
        self.assertEqual(0, len(mail.outbox))

    def test_worker_directory_detail_and_contract_extension(self):
        work_order = self._create_work_order()
        original_end_date = work_order.end_date
        acceptance_response = self._accept_work_order(work_order)
        self.assertEqual(200, acceptance_response.status_code)
        acceptance = acceptance_response.json()
        worker_id = acceptance["worker_id"]
        cws_id = f"CWS-{worker_id:06d}"

        self._login_admin()
        list_response = self.admin_client.get(
            f"/api/workers?status=onboarding&search={cws_id}"
        )
        self.assertEqual(200, list_response.status_code)
        directory = list_response.json()
        self.assertEqual(1, len(directory["results"]))
        self.assertEqual(1, directory["summary"]["compliance_alerts"])
        record = directory["results"][0]
        self.assertEqual(worker_id, record["worker_id"])
        self.assertEqual(cws_id, record["cws_id"])
        self.assertEqual("review_required", record["compliance_status"])
        self.assertEqual(self.role.name, record["role"])
        self.assertEqual(self.supplier.name, record["supplier"])

        detail_response = self.admin_client.get(
            (
                f"/api/workers/{worker_id}"
                f"?work_order={work_order.id}"
            )
        )
        self.assertEqual(200, detail_response.status_code)
        detail = detail_response.json()
        self.assertEqual(1, len(detail["assignments"]))
        self.assertTrue(detail["permissions"]["can_extend_contract"])
        self.assertTrue(detail["permissions"]["can_offboard"])

        supplier_detail = self.client.get(
            (
                f"/api/workers/{worker_id}"
                f"?work_order={work_order.id}"
            )
        )
        self.assertEqual(200, supplier_detail.status_code)
        self.assertFalse(
            supplier_detail.json()["permissions"]["can_extend_contract"]
        )
        denied_extension = self.client.post(
            f"/api/workers/{worker_id}/contract/extend",
            {
                "work_order_id": work_order.id,
                "end_date": (
                    original_end_date + timedelta(days=30)
                ).isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(403, denied_extension.status_code)

        invalid_extension = self.admin_client.post(
            f"/api/workers/{worker_id}/contract/extend",
            {
                "work_order_id": work_order.id,
                "end_date": original_end_date.isoformat(),
            },
            content_type="application/json",
        )
        self.assertEqual(409, invalid_extension.status_code)

        new_end_date = original_end_date + timedelta(days=30)
        extension_response = self.admin_client.post(
            f"/api/workers/{worker_id}/contract/extend",
            {
                "work_order_id": work_order.id,
                "end_date": new_end_date.isoformat(),
                "notes": "Approved thirty-day extension.",
            },
            content_type="application/json",
        )
        self.assertEqual(200, extension_response.status_code)
        self.assertEqual(
            new_end_date.isoformat(),
            extension_response.json()["end_date"],
        )
        work_order.refresh_from_db()
        self.assertEqual(new_end_date, work_order.end_date)
        self.assertEqual(
            new_end_date.isoformat(),
            work_order.source_snapshot["contract_extensions"][-1][
                "new_end_date"
            ],
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="worker.contract_extended",
                object_id=str(detail["worker_engagement_id"]),
            ).exists()
        )

    def test_acceptance_rejects_an_existing_non_worker_tenant_account(self):
        work_order = self._create_work_order(
            worker_name="Lifecycle Admin",
            worker_email=self.admin.email,
        )

        response = self._accept_work_order(work_order)

        self.assertEqual(409, response.status_code)
        self.assertIn("non-worker account", response.json()["detail"])
        work_order.refresh_from_db()
        self.assertEqual(
            WorkOrder.SUPPLIER_ACCEPTANCE_PENDING,
            work_order.supplier_acceptance_status,
        )
        self.assertEqual(0, Engagement.objects.count())
        self.assertFalse(Worker.objects.filter(email=self.admin.email).exists())
        self.assertEqual(0, len(mail.outbox))
