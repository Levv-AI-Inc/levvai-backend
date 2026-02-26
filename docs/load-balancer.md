# Load Balancer (Brief)

## Diagram

```mermaid
flowchart LR
    U[Browser<br/>tenant.levvai.com] --> DNS[Cloud DNS<br/>*.levvai.com A]
    DNS --> IP[Global IP<br/>levvai-lb-ip]
    IP --> FR[Forwarding Rule :443<br/>levvai-lb-https-rule]
    FR --> PXY[Target HTTPS Proxy<br/>levvai-lb-https-proxy]
    PXY --> MAP[URL Map<br/>levvai-lb-urlmap]

    MAP -->|Default: all other paths| FEBE[Backend Service<br/>levvai-lb-fe-backend]
    MAP -->|/admin/* /api/* /django-admin/* /tasks/*<br/>/auth/workos/* /auth/password/*<br/>/auth/user* /auth/logout*| BEBE[Backend Service<br/>levvai-lb-be-backend]

    FEBE --> FENEG[Serverless NEG<br/>levvai-lb-fe-neg]
    BEBE --> BENEG[Serverless NEG<br/>levvai-lb-be-neg]

    FENEG --> FE[Cloud Run Service<br/>levvai-website]
    BENEG --> BE[Cloud Run Service<br/>levvai-backend]
```

## What routes where

- Backend (`levvai-backend`):
  - `/admin/*`
  - `/api/*`
  - `/django-admin/*`
  - `/tasks/*`
  - `/auth/workos/*`
  - `/auth/password/*`
  - `/auth/user*`
  - `/auth/logout*`
- Frontend (`levvai-website`):
  - everything else (default route)

## Key resources

- URL map: `levvai-lb-urlmap`
- HTTPS proxy: `levvai-lb-https-proxy`
- Forwarding rule: `levvai-lb-https-rule`
- Global IP: `levvai-lb-ip`
- Frontend NEG/backend:
  - `levvai-lb-fe-neg` -> `levvai-lb-fe-backend` -> `levvai-website`
- Backend NEG/backend:
  - `levvai-lb-be-neg` -> `levvai-lb-be-backend` -> `levvai-backend`

## Minimal checks

```bash
# URL map + path rules
gcloud compute url-maps describe levvai-lb-urlmap --format='yaml(pathMatchers,hostRules)'

# NEG targets
gcloud compute network-endpoint-groups describe levvai-lb-fe-neg --region us-east1 --format='value(cloudRun.service)'
gcloud compute network-endpoint-groups describe levvai-lb-be-neg --region us-east1 --format='value(cloudRun.service)'

# Sanity requests
curl -i https://test.levvai.com/
curl -i https://test.levvai.com/auth/user
curl -i -X POST https://test.levvai.com/auth/logout
```

## Notes

- Wildcard DNS should point `*.levvai.com` to `levvai-lb-ip`.
- If tenant host returns Google/Cloud Run 404 for frontend routes, verify URL map default route and frontend host rewrite.
