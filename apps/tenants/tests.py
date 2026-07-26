from unittest.mock import call, patch

from django.test import SimpleTestCase

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
