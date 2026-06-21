# Roles API

Tenant-scoped role definitions created by admins/managers as the base entity for future rate configuration.

## Endpoints

- `GET /api/roles`
- `POST /api/roles`
- `GET /api/roles/{id}`
- `PATCH /api/roles/{id}`
- `DELETE /api/roles/{id}`

Equivalent `/admin/...` routes are also available for admin-facing clients.

## Role payload

```json
{
  "name": "Senior Developer",
  "description": "Senior software engineering role for the Toronto market.",
  "country": "CA",
  "region": "ON",
  "city": "Toronto",
  "default_currency": "CAD",
  "default_unit": "hour",
  "is_active": true
}
```

## Response shape

```json
{
  "id": 12,
  "code": "senior-developer-ca-on-toronto",
  "name": "Senior Developer",
  "description": "Senior software engineering role for the Toronto market.",
  "country": "CA",
  "region": "ON",
  "city": "Toronto",
  "location_label": "Toronto, ON, CA",
  "default_currency": "CAD",
  "default_unit": "hour",
  "is_active": true,
  "created_at": "2026-04-10T12:00:00Z",
  "updated_at": "2026-04-10T12:00:00Z"
}
```

## Filters

`GET /api/roles` supports:

- `search`
- `is_active`
- `country`
- `region`
- `city`
- `default_currency`
- `default_unit`
