# Contract baseline

This note records the Supplier and User Management MVP baseline. It does not
change endpoint behavior.

## Schema ownership

The deployment uses `django-tenants` with the following ownership:

- Shared schema: Django framework apps, `tenants`, `accounts`, and `timesheets`.
  Shared identity, tenant memberships, supplier/worker invitations, worker
  profiles, and worker engagements therefore remain globally stored with
  explicit tenant references where applicable.
- Tenant schemas: `audit`, `approvals`, `intake`, `masterdata`, `policies`,
  `rates`, and `workorders`. Their records are isolated by the active database
  schema selected from the request tenant.

The canonical source is `SHARED_APPS` and `TENANT_APPS` in
`levvai/settings.py`. Moving an app between these lists is a data migration and
is outside the contract-baseline iteration.

## Rate-card ownership

`masterdata.RateCard` is legacy. `rates.RateCard` is the supported pricing
model and `/api/rate-cards/` contract. The root URL configuration includes
`apps.rates.urls` before `apps.masterdata.urls`, which preserves that route
resolution while legacy records remain available for a later migration.

The hard-coded Next.js `/admin/rates` screen is prototype-only. Production
rate-card work must extend the API-backed `/admin/rates/cards` implementation.

## Rollout flags

The following settings are rollout controls and default to disabled:

- `user_detail_v2`
- `user_access_management`
- `supplier_detail_v2`
- `supplier_coverage`
- `supplier_rate_cards_v2`
- `agreement_extraction`
- `deterministic_rate_resolution`

Flags never replace Django authorization or tenant/supplier record scoping.
