# VirtueWise VPS

An open-source, self-hostable VPS control plane designed to start on one computer and grow into a multi-node hosting platform.

> **Project status: early development.** The repository includes a Windows Hyper-V and Linux libvirt provider prototype, authenticated outbound node heartbeats, resource-aware multi-node provisioning dispatch, and node-routed VM lifecycle operations. It has not been validated on a physical Hyper-V host and is not production-ready.

## Current capabilities

- FastAPI control-plane API and lightweight dashboard
- Username/password accounts with PBKDF2 password hashing, signed expiring sessions, HttpOnly cookies and CSRF checks
- Administrator-managed accounts, admin/user roles, per-user VM ownership, instance-count quotas and an application audit log
- SQLite metadata store and Docker Compose development setup
- Windows Hyper-V agent with bearer-token authentication and allow-listed VM operations
- Dashboard agent health, VM inventory and VM start/stop/create actions
- VM console launch on the Windows host using Hyper-V VMConnect (not browser-based streaming)
- Optional Ubuntu cloud-init NoCloud seed ISO with SSH public-key injection and autoinstall configuration
- Hyper-V virtual NIC, MAC address and guest IP inventory
- VM deletion with exact-name confirmation; disk deletion is separately opt-in and restricted to the managed VM root
- Host CPU, physical RAM and VM-storage-volume capacity sampling
- Node heartbeat and inventory reporting, resource-aware multi-node placement with transactional capacity reservations, and durable asynchronous provisioning jobs
- Outbound authenticated node command polling for VM create/start/stop/delete/console operations; no inbound node-agent listener is required
- Multi-node VM inventory, switch and monitoring aggregation; per-node VM placement and lifecycle routing, including switch-aware node affinity
- Admin dashboard for one-time node enrollment, credential revocation, customer-safe node summaries and isolated virtual network management with one-customer network assignments; customers see only assigned switches
- CPU, RAM and storage quotas with transactional allocation reservations
- First-party read-only MCP server with scoped per-customer tokens and Streamable HTTP, plus a signed, versioned provider SDK with administrator-controlled package lifecycle
- Searchable developer portal at `/developers`

## Development prerequisites

- Git
- Docker Engine and Docker Compose plugin for dashboard-only development
- Python 3.11+ for the backend and node agent
- Windows 11 Pro, Enterprise, or Education with Hyper-V enabled for the Hyper-V provider
- Hardware virtualization enabled in UEFI/BIOS
- Windows ADK Deployment Tools (oscdimg.exe) for cloud-init seed ISO generation

Windows 11 Home does not support this Hyper-V provider. You can still develop the control plane on Home and connect a supported host later.

## Quick start — dashboard only

    git clone https://github.com/virtuewise/VPS.git
    cd VPS
    Copy-Item .env.example .env
    # Edit .env and set a unique VPS_ADMIN_PASSWORD and a random VPS_SESSION_SECRET
    py -c "import secrets; print(secrets.token_hex(32))"
    docker compose up --build

Open the dashboard at http://localhost:8000 and sign in with username `admin` and the password configured as VPS_ADMIN_PASSWORD in .env. Stop with Ctrl+C or run docker compose down.

## Connect a local Windows Hyper-V node

Follow the Windows 11 Hyper-V setup guide, then the control-plane/agent setup guide. For the current local-only setup, run the agent and backend directly on Windows with the agent bound to 127.0.0.1.

See docs/HYPERV-ADVANCED-FEATURES.md for console, cloud-init, SSH, network reporting, deletion safeguards and monitoring setup. Visit `/developers` for the searchable developer portal, provider SDK, signed package workflow and MCP integration guide.

Use the administrator's **Customer accounts** panel to create customers, set VM-count quotas, change roles, suspend/reactivate accounts, transfer VM ownership and review recent audit events. See docs/ACCOUNTS-AND-ADMINISTRATION.md for account setup and API details.

## Security and limitations

- Keep the current agent and control plane bound to loopback. Do not expose them publicly.
- The dashboard requires a configured administrator password and session-signing secret. Customer accounts have ownership checks and VM/CPU/RAM/storage quotas. Billing, MFA, password recovery and production hardening remain outstanding.
- The Compose port is bound to localhost by default. For remote access, use a properly configured HTTPS reverse proxy, set VPS_SESSION_COOKIE_SECURE=true, and apply firewall restrictions. Do not expose the development server directly.
- Node enrollment uses single-use expiring credentials, per-node tokens, heartbeats and administrator revocation. Mutual TLS, multi-process queue coordination, external security review and production-grade rate limiting remain outstanding.
- The console action launches VMConnect on the Windows host; it is not a browser console.
- Cloud-init requires a compatible Ubuntu Server ISO and Windows ADK oscdimg.exe. Test unattended installation in a disposable VM first.
- Guest IP reporting depends on Hyper-V integration services and guest network configuration.
- Resource metrics are point-in-time host samples, not historical monitoring or billing data.
- Deleting a VM is destructive. Disk deletion is opt-in, but always back up guest data first.

## Cost and hardware expectations

The software is intended to be free and open source. Running real VPS instances still requires CPU, RAM, storage, networking and electricity. This project is a local development prototype, not a secure public hosting service.

## License

MIT. See LICENSE.

## Developer portal and provider SDK

Open `/developers` in the running dashboard for the searchable developer portal. The v1 provider SDK includes versioned manifests, explicit capabilities, conformance tests, Ed25519-signed packages, trust-store verification, a rootless Docker worker profile, and install/enable/disable/upgrade/rollback/uninstall lifecycle commands. See [Provider SDK](docs/PROVIDER-SDK.md) and [Plugin architecture](docs/PLUGIN-SYSTEM.md). Third-party providers require a Linux node with rootless Docker configured and an image pinned by digest; the Windows Hyper-V provider remains built in.

## Virtualization providers

An experimental Linux KVM/libvirt provider is available alongside Windows Hyper-V. See [Linux KVM setup](docs/LINUX-KVM-LIBVIRT.md). It has automated unit tests but has not yet passed real-host acceptance testing.
