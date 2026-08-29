#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

cd "$PROJECT_DIR"

LOCAL_ADMIN_EMAIL=${LOCAL_ADMIN_EMAIL:-admin@local.levvai.test}
export LOCAL_ADMIN_EMAIL
LOCAL_WORKER_EMAIL=${LOCAL_WORKER_EMAIL:-worker@local.levvai.test}
export LOCAL_WORKER_EMAIL
LOCAL_WORKER_PASSWORD=${LOCAL_WORKER_PASSWORD:-WorkerPassword123!}
export LOCAL_WORKER_PASSWORD

. "$SCRIPT_DIR/local-env.sh"

echo "Starting local PostgreSQL..."
docker compose \
  --env-file scripts/local-compose.env \
  -f docker-compose.local.yml \
  up -d --wait

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi

PYTHON=.venv/bin/python
REQUIREMENTS_MARKER=.venv/.requirements-installed

if [ ! -f "$REQUIREMENTS_MARKER" ] || [ requirements.txt -nt "$REQUIREMENTS_MARKER" ]; then
  echo "Installing backend dependencies..."
  "$PYTHON" -m pip install -r requirements.txt
  touch "$REQUIREMENTS_MARKER"
fi

echo "Applying shared database migrations..."
"$PYTHON" manage.py migrate_schemas --shared

if "$PYTHON" manage.py shell -c '
import os

from apps.accounts.models import Membership

email = os.environ["LOCAL_ADMIN_EMAIL"].strip().lower()
exists = Membership.objects.filter(
    user__email__iexact=email,
    tenant__schema_name="local",
    role=Membership.ROLE_ADMIN,
    status=Membership.STATUS_ACTIVE,
    is_active=True,
).exists()
raise SystemExit(0 if exists else 1)
'; then
  echo "Local admin ready: $LOCAL_ADMIN_EMAIL"
else
  echo "Creating local tenant and admin account..."
  "$PYTHON" manage.py bootstrap_local_dev --email "$LOCAL_ADMIN_EMAIL"
fi

echo "Applying tenant database migrations..."
"$PYTHON" manage.py migrate_schemas --tenant

echo "Seeding local demo data..."
"$PYTHON" manage.py seed_local_data --admin-email "$LOCAL_ADMIN_EMAIL"

echo ""
echo "Backend: http://127.0.0.1:8000"
echo "Login:   $LOCAL_ADMIN_EMAIL"
echo "Worker:  $LOCAL_WORKER_EMAIL / $LOCAL_WORKER_PASSWORD"
echo "Stop the server with Ctrl+C. PostgreSQL will remain available."
echo ""

exec "$PYTHON" manage.py runserver 127.0.0.1:8000
