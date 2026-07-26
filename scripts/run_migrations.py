import os
import sys
from pathlib import Path

import django
from django.core.management import call_command


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def ensure_project_root_on_path():
    """Make project packages importable when this file is run directly."""
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def migrate_all_schemas():
    """Apply migrations to both the public schema and every tenant schema."""
    call_command("migrate_schemas", "--shared", interactive=False)
    call_command("migrate_schemas", "--tenant", interactive=False)


def main():
    ensure_project_root_on_path()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "levvai.settings")
    django.setup()
    migrate_all_schemas()


if __name__ == "__main__":
    sys.exit(main())
