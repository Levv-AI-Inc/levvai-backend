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

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate_schemas --shared
python manage.py runserver
```

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
gcloud run deploy levvai-backend --source . --region us-east1 --no-allow-unauthenticated
```

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

