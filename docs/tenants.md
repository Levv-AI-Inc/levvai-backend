# Create a Tenant (Short)

## Prereqs (one-time)
- Cloud Run service is reachable (private is fine).
- The Cloud Run default URL (or your API domain) is mapped to the **public** tenant in `tenants_domain`.
- Domain ownership is verified in Search Console and the Cloud Run runtime service account is an **Owner**.
- Cloud Run service account has Cloud Tasks + Cloud DNS permissions.

## Steps
1. Create the tenant (enqueues provisioning):
```bash
TOKEN=$(gcloud auth print-identity-token)
curl -i \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme","schema_name":"acme","domain":"acme.levvai.com"}' \
  https://levvai-backend-245852154678.us-east1.run.app/admin/tenants
```

2. Check readiness:
```bash
gcloud beta run domain-mappings describe \
  --domain=acme.levvai.com \
  --region us-east1 \
  --format='yaml(status)'
```

3. Test the new domain:
```bash
TOKEN=$(gcloud auth print-identity-token)
curl -i -H "Authorization: Bearer $TOKEN" https://acme.levvai.com/django-admin/
```

## Optional checks
You can also check these in the Google Cloud Console (Cloud Tasks, Cloud DNS, and Cloud Run > Domain mappings).

- Verify a Cloud Tasks job was created:
```bash
gcloud tasks tasks list \
  --queue tenant-domain-provision \
  --location us-east1
```

- Verify DNS records were written in Cloud DNS:
```bash
gcloud dns record-sets list \
  --zone levvai-com \
  --name acme.levvai.com.
```

- Certificate provisioning can take a few minutes. The domain mapping status will show:
  - `CertificatePending` while waiting
  - `Ready: True` once the certificate is issued

```bash
gcloud beta run domain-mappings describe \
  --domain=acme.levvai.com \
  --region us-east1 \
  --format='yaml(status)'
```

## Error/Troubleshooting
If provisioning fails, retry only the domain mapping:
```bash
TOKEN=$(gcloud auth print-identity-token)
curl -i \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain":"acme.levvai.com"}' \
  https://levvai-backend-245852154678.us-east1.run.app/tasks/provision-domain
```
