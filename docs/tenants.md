# Create a Tenant (Short)

## Prereqs (one-time)
- Set `TENANT_DNS_MODE=wildcard` on the backend to skip per-tenant DNS provisioning.
- Cloud Run backend deployed.
- A wildcard DNS record points `*.levvai.com` to the HTTPS Load Balancer.
- The load balancer routes `/auth/workos/*`, `/auth/password/*`, `/admin/*`, `/api/*`, `/django-admin/*`, `/tasks/*` to the backend and everything else to the frontend.
- WorkOS SSO redirect URIs are registered per tenant (if using SSO).

## Steps
1. Create the tenant (no per-tenant DNS provisioning when `TENANT_DNS_MODE=wildcard`):
```bash
TOKEN=$(gcloud auth print-identity-token)
curl -i \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Acme","schema_name":"acme","domain":"acme.levvai.com"}' \
  https://levvai-backend-245852154678.us-east1.run.app/admin/tenants
```

2. Test the new tenant domain (frontend should load; auth routes go to backend):
```bash
curl -i https://acme.levvai.com/
```

3. `Login` URL behavior:
- `https://acme.levvai.com/auth/login` should load the frontend login page.
- `https://acme.levvai.com/login` should redirect to `/auth/login` (frontend alias route).

4. (Optional) Test Django admin auth via tenant domain:
```bash
TOKEN=$(gcloud auth print-identity-token)
curl -i -H "Authorization: Bearer $TOKEN" https://acme.levvai.com/django-admin/
```

## Optional checks
You can also check these in the Google Cloud Console.

- Verify the wildcard DNS record exists:
```bash
gcloud dns record-sets list \
  --zone levvai-com \
  --name '*.levvai.com.'
```

- Verify the Load Balancer URL map routes only backend auth API paths to the backend:
```bash
gcloud compute url-maps describe levvai-lb-urlmap --format='yaml(pathMatchers)'
```

- Snapshot current LB config for rollback docs:
```bash
gcloud compute url-maps describe levvai-lb-urlmap --format=yaml > lb-urlmap.snapshot.yaml
gcloud compute target-https-proxies describe levvai-lb-https-proxy --format=yaml > lb-https-proxy.snapshot.yaml
```

## Error/Troubleshooting
If the tenant domain loads the frontend but API calls return 404, the backend likely does not have a `tenants_domain` record for that host.

If `/auth/workos/login` returns 403, Cloud Run is still private. Ensure `allUsers` has `roles/run.invoker` and deploy with `--allow-unauthenticated`.
