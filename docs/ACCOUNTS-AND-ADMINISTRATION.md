# Accounts and administration

This milestone adds the account foundation for the future multi-tenant control plane.

## First administrator

On first startup, the application creates the `admin` account from `VPS_ADMIN_PASSWORD`. Use the login form with username `admin` and that configured password. Passwords are stored as PBKDF2-HMAC-SHA256 hashes with per-user random salts; the configured password is not stored in plaintext.

The account is bootstrapped only when no `admin` account exists. Changing `VPS_ADMIN_PASSWORD` after initial database creation does not reset the stored account password.

## Create accounts

Only an administrator can list or create accounts:

- `GET /api/users`
- `POST /api/users`

Example request body:

```json
{
  "username": "customer1",
  "password": "use-a-unique-password-at-least-16-characters",
  "role": "user",
  "vm_quota": 3
}
```

Allowed roles are `admin` and `user`. Usernames are 3–32 characters and limited to letters, digits, underscore, dot and hyphen. New passwords must be at least 16 characters. The `vm_quota` value is stored as an account policy setting; enforcement and tenant-isolated VM ownership are not yet implemented.

## Audit events

Administrators can retrieve the most recent account audit events with `GET /api/audit?limit=100`. The current log records successful sign-ins, sign-outs and account creation. It is an initial application audit trail, not a tamper-proof security log.

## Current limitations

- VM APIs and dashboard management remain administrator-only.
- Tenant-specific VM ownership and quota enforcement are the next implementation step.
- User activation/deactivation, password reset, role editing, invitation flow, rate limiting and MFA are not implemented.
- Existing installations keep the existing SQLite database. Back it up before upgrades.
- Keep the dashboard loopback-only. This feature is a development foundation, not production-ready multi-tenant hosting.
