# Approval Chains API

Tenant-scoped approval chain configuration for matching requests against condition sets and resolving ordered approver steps.

## Endpoints

- `GET /api/approval-chains`
- `POST /api/approval-chains`
- `GET /api/approval-chains/{id}`
- `PATCH /api/approval-chains/{id}`
- `DELETE /api/approval-chains/{id}`
- `GET /api/approval-chains/catalog`
- `POST /api/approval-chains/simulate`

Equivalent `/admin/...` routes are also available for admin-facing clients.

## Approval chain payload

```json
{
  "name": "Software Requisition Chain",
  "description": "Routes software-related requests through procurement and finance approvers.",
  "is_active": true,
  "priority": 10,
  "match_strategy": "all",
  "conditions": [
    {
      "sequence": 1,
      "field_key": "commodity",
      "operator": "equals",
      "value": "software"
    },
    {
      "sequence": 2,
      "field_key": "budget_amount",
      "operator": "gte",
      "value": "1000"
    }
  ],
  "steps": [
    {
      "sequence": 1,
      "step_type": "specific_user",
      "approver": 12,
      "amount": "1000.00",
      "currency": "USD"
    },
    {
      "sequence": 2,
      "step_type": "specific_user",
      "approver": 18,
      "amount": "5000.00",
      "currency": "USD"
    }
  ]
}
```

## Catalog

`GET /api/approval-chains/catalog` returns:

- all supported operators
- all built-in field definitions
- a wildcard `custom_fields.*` definition for dynamic request fields

## Simulation

Use `POST /api/approval-chains/simulate` to validate routing before the request model is integrated.

```json
{
  "payload": {
    "commodity": "software",
    "budget_amount": "2500",
    "country": "CA",
    "custom_fields": {
      "project_type": "ERP rollout"
    }
  },
  "include_inactive": false,
  "include_non_matches": false
}
```

The response returns matching chains plus per-condition evaluation details and resolved approver steps.
