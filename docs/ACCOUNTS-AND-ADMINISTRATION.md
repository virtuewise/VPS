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

Allowed roles are `admin` and `user`. Usernames are 3–32 characters and limited to letters, digits, underscore, dot and hyphen. New passwords must be at least 16 characters. The `vm_quota` value is enforced when a non-admin creates a VM. A quota of 0 disables VM creation; administrators are unlimited. VM ownership is recorded when the control plane successfully creates a VM.

## Audit events

Administrators can retrieve the most recent account audit events with `GET /api/audit?limit=100`. The current log records successful sign-ins, sign-outs, account creation and VM create/start/stop/delete actions. It is an initial application audit trail, not a tamper-proof security log.

## Current limitations

- Non-admin users can create, view, start, stop, open the host console for, and delete only VMs recorded as theirs. Administrators can manage all VMs.
- VMs that existed before ownership tracking was introduced remain visible to administrators only; there is not yet an ownership-transfer/claim workflow.
- The quota counts recorded VM ownership rows; it is not a CPU, RAM, disk-capacity or billing quota.
- User activation/deactivation, password reset, role editing, invitation flow, rate limiting and MFA are not implemented.
- Existing installations keep the existing SQLite database. Back it up before upgrades.
- Keep the dashboard loopback-only. This feature is a development foundation, not production-ready multi-tenant hosting.
