# Intake API (Milestone 2)

## State machine

- `draft -> submitted`
- `submitted -> approved`
- `submitted -> rejected`
- Any other transition returns `409`.

## Endpoints

- `GET /approvals/dashboard` (current user: pending requests + approval queue)
- `GET /intake` (supports `?status=` and `?mine=true`)
- `POST /intake/draft`
- `PATCH /intake/{id}`
- `GET /intake/{id}`
- `POST /intake/{id}/submit`
- `POST /intake/{id}/approve`
- `POST /intake/{id}/reject`
- `GET /intake/{id}/approval-preview`
- `POST /nova/intake/confidence`

Equivalent `/api/...` routes are also available for LB setups that route `/api/*` to backend.

### Approvals dashboard response

`GET /api/approvals/dashboard`

```json
{
  "my_pending_requests": [],
  "my_approval_queue": [],
  "meta": {
    "is_approver": true,
    "role": "finance"
  }
}
```

## Validation behavior

- Draft updates return `warnings` (soft validation).
- Submit runs hard validation and blocks with:

```json
{
  "errors": [
    {"field": "cost_center_id", "code": "required", "message": "Cost Center Id is required."}
  ]
}
```

## Notes

- On submit, an immutable `IntakeSnapshot` is created (`version=1`).
- Approval preview is placeholder routing by engagement type (policy engine is fast-follow).
- Nova confidence returns strict JSON:
  - `missing_fields[]`
  - `weak_fields[]`
  - `confidence_score`
