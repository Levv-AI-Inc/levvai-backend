# Suppliers API

## Covered requirements

- `MD-09`: Supplier master data (contact/tax/diversity).
- Supplier onboarding flow: create supplier -> invite supplier contact -> supplier registers via link.
- Cross-tenant supplier access for one identity: an existing user can accept a supplier invite in another tenant without creating a new tenant.

## Endpoints

- `GET /api/suppliers/`
- `POST /api/suppliers/`
- `PATCH /api/suppliers/{id}/`
- `POST /api/suppliers/{id}/invite/`
- `POST /auth/password/register` (supports `invite_token`)

Equivalent `/admin/suppliers/...` endpoints remain available.

## Invite flow

1. Internal user creates supplier record.
2. Internal user sends invite with `POST /api/suppliers/{id}/invite/`.
3. API returns a `registration_link` with `invite_token`.
4. Supplier opens link and submits `POST /auth/password/register`.
5. If the email already exists, the same identity is linked to the current tenant with a supplier membership.
