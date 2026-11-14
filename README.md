# VirtueWise VPS

An open-source, self-hostable VPS control plane designed to start on one computer and grow into a multi-node hosting platform.

> **Project status: early development.** The current MVP provides a control-plane API and a lightweight dashboard foundation. It does not yet create or expose production virtual machines.

## Goals

- Run the control plane locally with no paid cloud dependencies.
- Manage compute nodes from one dashboard.
- Provision Linux virtual machines through a least-privilege host agent (planned).
- Support KVM/QEMU on Linux; keep provider integrations modular.
- Make self-hosting, security, and transparent operating costs first-class.

## Development prerequisites

- Docker Engine and Docker Compose plugin
- Git
- (Optional for native backend development) Python 3.12+

The first development setup uses Docker Compose and SQLite so it can run on a typical computer without a separate database service.

## Quick start

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

## Repository layout

```
backend/       FastAPI control-plane API
  app/         Application code
frontend/      Dashboard assets (served by the API in this first MVP)
docs/          Architecture and development notes
docker-compose.yml
```

## Roadmap

- [x] Initial repository and local development scaffold
- [x] Health endpoint and starter dashboard
- [ ] Persistent users, sessions, and role-based access control
- [ ] Compute-node registration and heartbeat
- [ ] Linux host agent with authenticated, allow-listed operations
- [ ] KVM/QEMU VM lifecycle management
- [ ] VM networking, storage, console, and SSH key management
- [ ] Usage metrics, quotas, audit logs, and backups
- [ ] Multi-node scheduling and public documentation

## Security principles

- Never expose the Docker socket or unrestricted shell commands to the web API.
- The future host agent must use authenticated requests, explicit operation allow-lists, resource limits, and audit logging.
- Do not expose the control-plane dashboard directly to the public internet until authentication, TLS, rate limiting, and security review are complete.
- Treat VM provisioning as privileged infrastructure code; test in disposable environments first.

## Cost and hardware expectations

The software is intended to be free and open source. Running real VPS instances still requires available CPU, RAM, storage, networking, and electricity. A laptop can run the development dashboard; reliable public hosting requires a Linux host with hardware virtualization and suitable network connectivity.

## License

Apache-2.0. See [LICENSE](LICENSE).
