# Accounts and administration

This milestone provides the initial multi-user control-plane account and administration workflow.

## First administrator

On first startup, the application creates the `admin` account from `VPS_ADMIN_PASSWORD`. Use the login form with username `admin` and that configured password. Passwords are stored as PBKDF2-HMAC-SHA256 hashes with per-user random salts; the configured password is not stored in plaintext.

The account is bootstrapped only when no `admin` account exists. Changing `VPS_ADMIN_PASSWORD` after initial database creation does not reset the stored account password.

## Create accounts

Administrators can manage accounts from the **Customer accounts** panel or through the API:

- `GET /api/users` — list accounts
- `POST /api/users` — create an account
- `PATCH /api/users/{id}` — change role, VM-count quota or active/suspended state
- `DELETE /api/users/{id}` — delete an account only after all its VM ownership records are transferred or removed
- `POST /api/users/{id}/transfer-vm?vm_name={name}` — transfer one recorded VM to an active customer account
- `GET /api/audit?limit=100` — view recent audit events

The dashboard prevents deleting the currently signed-in administrator. The API also prevents self-deletion and prevents removing or disabling the last active administrator.

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

Administrators can retrieve the most recent account audit events with `GET /api/audit?limit=100`. The current log records successful sign-ins, sign-outs, account creation/updates/deletion, ownership transfers and VM create/start/stop/delete actions. It is an initial application audit trail, not a tamper-proof security log.

## Current limitations

- Non-admin users can create, view, start, stop, open the host console for, and delete only VMs recorded as theirs. Administrators can manage all VMs.
- VMs that existed before ownership tracking was introduced remain visible to administrators only. Administrators can now transfer a recorded VM ownership row to an active customer using the dashboard transfer form; this does not migrate the VM between Hyper-V hosts.
- The quota counts recorded VM ownership rows; it is not a CPU, RAM, disk-capacity or billing quota.
- Password reset/change, email invitations, MFA, billing and resource-based quotas are not implemented. Sign-in failures are throttled after five attempts per username and client IP in a rolling 15-minute window.
- Existing installations keep the existing SQLite database. Back it up before upgrades.
- Keep the dashboard loopback-only. This feature is a development foundation, not production-ready multi-tenant hosting.


## Run account API tests

From the repository root on Windows:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The integration test exercises administrator login, CSRF enforcement, account creation, customer role isolation, VM-count quota rejection, account suspension, deletion safeguards, audit access and sign-in throttling. It uses a temporary SQLite database and does not contact a Hyper-V host.
