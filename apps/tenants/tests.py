from collections import Counter
from types import SimpleNamespace
from unittest.mock import call, patch
from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.accounts.models import WorkerEngagement, WorkerProfile
from apps.tenants.management.commands.seed_local_data import Command as SeedLocalDataCommand
from scripts import run_migrations


class MigrationRunnerTests(SimpleTestCase):
    @patch.object(run_migrations.sys, "path", ["/not-the-project-root"])
    def test_adds_project_root_when_launched_as_a_script(self):
        run_migrations.ensure_project_root_on_path()

        self.assertEqual(
            run_migrations.sys.path[0],
            str(run_migrations.PROJECT_ROOT),
        )

    @patch("scripts.run_migrations.call_command")
    def test_migrates_shared_and_existing_tenant_schemas(self, call_command):
        run_migrations.migrate_all_schemas()

        self.assertEqual(
            call_command.call_args_list,
            [
                call("migrate_schemas", "--shared", interactive=False),
                call("migrate_schemas", "--tenant", interactive=False),
            ],
        )


class LocalWorkerSeedTests(SimpleTestCase):
    def test_seed_local_worker_account_creates_global_profile_and_tenant_engagement(self):
        command = SeedLocalDataCommand()
        command.refresh = False
        command.created = Counter()
        command.existing = Counter()
        command.local_worker_email = "worker@local.levvai.test"
        command.local_worker_password = "WorkerPassword123!"
        command.local_worker_name = "Jordan Reyes"

        tenant = SimpleNamespace(id=42, name="Local Development")
        admin = SimpleNamespace(id=7)
        supplier = SimpleNamespace(id=3, name="Apex Talent Partners")
        role = SimpleNamespace(name="Software Engineer")
        context = {
            "suppliers": {"SUP-APEX": supplier},
            "roles": {"ROLE-SWE-NYC": role},
        }

        user = Mock(password="oldhash")
        user.has_usable_password.return_value = False
        User = Mock(return_value=user, AUTH_PASSWORD="password")
        User.objects.filter.return_value.order_by.return_value.first.return_value = None

        worker_profile = Mock(status=WorkerProfile.STATUS_DISABLED)
        worker_profile_manager = Mock()
        worker_profile_manager.get_or_create.return_value = (worker_profile, True)

        engagement = Mock()
        worker_engagement_manager = Mock()
        worker_engagement_manager.update_or_create.return_value = (engagement, True)

        with (
            patch(
                "apps.tenants.management.commands.seed_local_data.get_user_model",
                return_value=User,
            ),
            patch(
                "apps.tenants.management.commands.seed_local_data.WorkerProfile.objects",
                worker_profile_manager,
            ),
            patch(
                "apps.tenants.management.commands.seed_local_data.WorkerEngagement.objects",
                worker_engagement_manager,
            ),
            patch("apps.tenants.management.commands.seed_local_data.record_password_history") as record_history,
        ):
            result = command._seed_local_worker_account(tenant, admin, context)

        self.assertIs(result, engagement)
        User.assert_called_once_with(
            username="worker@local.levvai.test",
            email="worker@local.levvai.test",
        )
        user.set_password.assert_called_once_with("WorkerPassword123!")
        record_history.assert_called_once_with(user, tenant)
        worker_profile_manager.get_or_create.assert_called_once_with(
            user=user,
            defaults={
                "status": WorkerProfile.STATUS_ACTIVE,
                "preferred_name": "Jordan Reyes",
            },
        )
        worker_engagement_manager.update_or_create.assert_called_once()
        engagement_kwargs = worker_engagement_manager.update_or_create.call_args.kwargs
        self.assertEqual(worker_profile, engagement_kwargs["worker_profile"])
        self.assertEqual(tenant, engagement_kwargs["tenant"])
        self.assertEqual(WorkerEngagement.TYPE_WORK_ORDER, engagement_kwargs["engagement_type"])
        self.assertIsNone(engagement_kwargs["work_order_id"])
        self.assertEqual("WO-LOCAL-WORKER-001", engagement_kwargs["work_order_number"])
        self.assertEqual(WorkerEngagement.STATUS_ACTIVE, engagement_kwargs["defaults"]["status"])
        self.assertEqual("Apex Talent Partners", engagement_kwargs["defaults"]["supplier_name"])
        self.assertEqual("Software Engineer", engagement_kwargs["defaults"]["role_name"])
        self.assertEqual(1, command.created["User"])
        self.assertEqual(1, command.created["WorkerProfile"])
        self.assertEqual(1, command.created["WorkerEngagement"])
