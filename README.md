# VirtueWise VPS

An open-source, self-hostable VPS control plane designed to start on one computer and grow into a multi-node hosting platform.

> **Project status: early development.** The repository now includes a Windows Hyper-V node-agent scaffold and an optional single-node control-plane integration. It has not yet been validated on a physical Hyper-V host and is not production-ready.

## Current capabilities

- FastAPI control-plane API and lightweight dashboard
- SQLite metadata store and Docker Compose development setup
- Windows Hyper-V agent with bearer-token authentication and allow-listed VM operations
- Dashboard agent health and VM inventory when connected
- Dashboard VM start/stop actions
- Control-plane API proxy for VM create/list/start/stop/delete and virtual-switch inventory

## Development prerequisites

- Git
- Docker Engine and Docker Compose plugin for dashboard-only development
- Python 3.11+ for the backend and node agent
- Windows 11 Pro, Enterprise, or Education with Hyper-V enabled for the Hyper-V provider
- Hardware virtualization enabled in UEFI/BIOS

Windows 11 Home does not support this Hyper-V provider. You can still develop the control plane on Home and connect a supported host later.

## Quick start — dashboard only

```bash
git clone https://github.com/virtuewise/VPS.git
cd VPS
docker compose up --build
```

Open:

- Dashboard: http://localhost:8000
- API health: http://localhost:8000/api/health
- API docs: http://localhost:8000/docs

Stop with `Ctrl+C`, or run `docker compose down`.

## Connect a local Windows Hyper-V node

Follow [Windows 11 Hyper-V setup](docs/WINDOWS-11-HYPERV.md), then [Control plane ↔ agent setup](docs/CONTROL-PLANE-AGENT.md).

For the current secure local-only setup, run the agent and backend directly on Windows with the agent bound to `127.0.0.1`. Docker Desktop cannot reach a host service bound only to host loopback; do not weaken the agent bind address to work around this.

## Repository layout

```
agent/          Windows Hyper-V node agent
backend/        FastAPI control-plane API
frontend/       Dashboard assets
docs/           Architecture, setup, and roadmap
docker-compose.yml
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for milestones and current completion status.

## Security principles

- Never expose the Docker socket or unrestricted shell commands to the web API.
- The node agent exposes only typed, allow-listed VM operations and requires a bearer token.
- Keep the current agent and control plane bound to loopback. Do not expose them publicly.
- Remote agent connections require HTTPS; secure enrollment, mutual TLS, user authentication, authorization, audit logging, and a security review are still outstanding.
- Treat VM provisioning as privileged infrastructure code; test in disposable environments first.

## Cost and hardware expectations

The software is intended to be free and open source. Running real VPS instances still requires available CPU, RAM, storage, networking, and electricity. A laptop can run the development control plane; reliable public hosting requires suitable always-on hardware and network connectivity.

## License

MIT. See [LICENSE](LICENSE).
