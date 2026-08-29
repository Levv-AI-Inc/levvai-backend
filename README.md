# LevvAI Backend (Milestone 1 Bootstrap)


## GCP

- Log explorer:  https://console.cloud.google.com/logs/query?project=levvai
- Cloud run service: https://console.cloud.google.com/run/detail/us-east1/levvai-backend

## Environment
Required env vars:
- `DATABASE_URL`
- `DJANGO_SECRET_KEY`

Optional:
- `DJANGO_DEBUG` (default: false)
- `DJANGO_ALLOWED_HOSTS` (default: *)
- `DJANGO_LOG_LEVEL` (default: INFO)
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `EMAIL_BACKEND` (default: `django.core.mail.backends.console.EmailBackend`)
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS` (default: true), `EMAIL_USE_SSL` (default: false)
- `DEFAULT_FROM_EMAIL` (default: `no-reply@levvai.com`)
- `SUPPLIER_INVITE_FROM_EMAIL` (defaults to `DEFAULT_FROM_EMAIL`)

## Local Development

Local login requires a non-public tenant because password login and session
validation reject the public schema. The frontend proxies same-origin backend
requests to Django when its `LOCAL_BACKEND_URL` is configured.

With Docker running, start the complete backend stack:

```bash
./scripts/run-local.sh
```

The first run:

- starts isolated PostgreSQL on `127.0.0.1:5433`;
- creates `.venv` and installs dependencies;
- applies shared and tenant migrations;
- creates the `local` tenant and localhost domain mappings;
- prompts for the local admin password;
- seeds linked demo company, supplier, approval, rate, workflow, and worker data;
- starts Django on `127.0.0.1:8000`.

Local development disables persistent Django database connections to prevent
the threaded development server from exhausting PostgreSQL connections.

Use this email at the frontend login page:

```text
admin@local.levvai.test
```

The seed command also creates a worker profile with one active local engagement:

```text
worker@local.levvai.test
WorkerPassword123!
```

Subsequent runs reuse the environment and admin account. To use a different
local admin email:

```bash
LOCAL_ADMIN_EMAIL=you@example.com ./scripts/run-local.sh
```

To use a different local worker account:

```bash
LOCAL_WORKER_EMAIL=worker@example.com \
LOCAL_WORKER_PASSWORD='WorkerPassword123!' \
./scripts/run-local.sh
```

In the frontend repository, set
`LOCAL_BACKEND_URL=http://127.0.0.1:8000`, start Next.js, and open:

```text
http://localhost:3000/auth/login
```

The local launcher reruns the idempotent seed command on startup. Existing
seeded records are preserved, while missing records are recreated. Run it
manually with:

```bash
source scripts/local-env.sh
.venv/bin/python manage.py seed_local_data
```

To restore seed-owned records to the current demo values:

```bash
source scripts/local-env.sh
.venv/bin/python manage.py seed_local_data --refresh
```

To reset the default local admin password:

```bash
source scripts/local-env.sh
.venv/bin/python manage.py bootstrap_local_dev \
  --email admin@local.levvai.test
```

Stop Django with `Ctrl+C`. PostgreSQL remains running so the next startup is
fast.

Stop PostgreSQL without deleting its data:

```bash
docker compose \
  --env-file scripts/local-compose.env \
  -f docker-compose.local.yml \
  down
```

Add `-v` to that command when you intentionally want to delete local data.

## Cloud Run
Container entrypoint:
```bash
gunicorn -c gunicorn.conf.py levvai.wsgi:application
```

Deploy (private):
```bash
gcloud auth login
gcloud config set account you@example.com
gcloud config set project levvai
gcloud run deploy levvai-backend --source . --region us-east1 --allow-unauthenticated
```

After deploying a revision that contains database migrations, run the migration
script once in the deployment environment before sending traffic to it:

```bash
python scripts/run_migrations.py
```

The script migrates both the shared schema and every existing tenant schema.
Running only `migrate_schemas --shared` does not update tenant apps such as
`apps.policies`.

Grant invoker access:
```bash
gcloud run services add-iam-policy-binding levvai-backend \
  --region us-east1 \
  --member="user:you@example.com" \
  --role="roles/run.invoker"
```

Set env vars (prefer secrets manager in production):
```bash
gcloud run services update levvai-backend \
  --region us-east1 \
  --set-env-vars DJANGO_SECRET_KEY=...,DATABASE_URL=...,DJANGO_ALLOWED_HOSTS=...
```

GCP project details:
- Project ID: `levvai`
- Region: `us-east1`

Supabase database:
```text
https://supabase.com/dashboard/project/sqkzocyxwhggojiecfor
```

Database connection (example format):
```text
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
```
Note: URL-encode special characters in PASSWORD (e.g., `@` -> `%40`, `#` -> `%23`).

## Tenant admin
Create tenant (public schema):
```
POST /admin/tenants
{
  "name": "Acme",
  "schema_name": "acme",
  "domain": "acme.levvai.com"
}
```
## Tenants and domains
See `docs/tenants.md` for tenant creation and domain provisioning.
