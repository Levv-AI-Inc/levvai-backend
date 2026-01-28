import os
import sys

import django
from django.core.management import call_command


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "levvai.settings")
    django.setup()
    call_command("migrate_schemas", "--shared", interactive=False)


if __name__ == "__main__":
    sys.exit(main())
