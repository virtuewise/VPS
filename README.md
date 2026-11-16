# VirtueWise VPS

An open-source, self-hostable VPS control plane designed to start on one computer and grow into a multi-node hosting platform.

> **Project status: early development.** The repository includes a Windows Hyper-V node-agent prototype and a single-node control-plane integration. It has not been validated on a physical Hyper-V host and is not production-ready.

## Current capabilities

- FastAPI control-plane API and lightweight dashboard
- Username/password accounts with PBKDF2 password hashing, signed expiring sessions, HttpOnly cookies and CSRF checks
- Initial administrator-only account creation and account-event audit APIs
- SQLite metadata store and Docker Compose development setup
- Windows Hyper-V agent with bearer-token authentication and allow-listed VM operations
- Dashboard agent health, VM inventory and VM start/stop/create actions
- VM console launch on the Windows host using Hyper-V VMConnect (not browser-based streaming)
- Optional Ubuntu cloud-init NoCloud seed ISO with SSH public-key injection and autoinstall configuration
- Hyper-V virtual NIC, MAC address and guest IP inventory
- VM deletion with exact-name confirmation; disk deletion is separately opt-in and restricted to the managed VM root
- Host CPU, physical RAM and VM-storage-volume capacity sampling

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

Open the dashboard at http://localhost:8000 and sign in with the password configured as VPS_ADMIN_PASSWORD in .env. Stop with Ctrl+C or run docker compose down.

## Connect a local Windows Hyper-V node

Follow the Windows 11 Hyper-V setup guide, then the control-plane/agent setup guide. For the current local-only setup, run the agent and backend directly on Windows with the agent bound to 127.0.0.1.

See docs/HYPERV-ADVANCED-FEATURES.md for console, cloud-init, SSH, network reporting, deletion safeguards and monitoring setup.

See docs/ACCOUNTS-AND-ADMINISTRATION.md for account setup and the current multi-user foundation.

## Security and limitations

- Keep the current agent and control plane bound to loopback. Do not expose them publicly.
- The dashboard now requires a configured administrator password and session-signing secret. Account and role scaffolding exists, but VM management is still administrator-only; per-user VM ownership and quota enforcement are not implemented.
- The Compose port is bound to localhost by default. For remote access, use a properly configured HTTPS reverse proxy, set VPS_SESSION_COOKIE_SECURE=true, and apply firewall restrictions. Do not expose the development server directly.
- Remote agent connections require HTTPS; secure enrollment, mutual TLS, role-based authorization, audit logging and a security review remain outstanding.
- The console action launches VMConnect on the Windows host; it is not a browser console.
- Cloud-init requires a compatible Ubuntu Server ISO and Windows ADK oscdimg.exe. Test unattended installation in a disposable VM first.
- Guest IP reporting depends on Hyper-V integration services and guest network configuration.
- Resource metrics are point-in-time host samples, not historical monitoring or billing data.
- Deleting a VM is destructive. Disk deletion is opt-in, but always back up guest data first.

## Cost and hardware expectations

The software is intended to be free and open source. Running real VPS instances still requires CPU, RAM, storage, networking and electricity. This project is a local development prototype, not a secure public hosting service.

## License

MIT. See LICENSE.
