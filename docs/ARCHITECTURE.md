# Architecture

## Current MVP

The first milestone is intentionally small:

- FastAPI serves the dashboard and JSON API.
- SQLite stores local control-plane metadata.
- Docker Compose runs the API with a persistent named volume.
- The dashboard reads health and compute-node inventory endpoints.

## Target architecture

```text
Browser
  |
  v
Control plane (API + dashboard) ---- PostgreSQL
  |
  | authenticated, signed node protocol
  v
Node agent (least privilege)
  |
  +---- libvirt / KVM / QEMU
  +---- VM storage pools
  +---- isolated virtual networks
```

The control plane should never run arbitrary host commands or mount the host Docker socket. A node agent will expose only typed, allow-listed actions and will validate resource limits before executing them.

## Development stages

1. Local dashboard and health API.
2. User identity, sessions, roles, and audit events.
3. Node enrollment using one-time enrollment tokens and mutual authentication.
4. Agent capabilities and heartbeat reporting.
5. VM lifecycle through libvirt on Linux hosts.
6. Network isolation, storage quotas, snapshots, and console access.
7. Multi-node placement and resource accounting.

## Laptop-first development

A standard laptop is enough for API and dashboard development. Actual KVM virtualization requires a compatible Linux host, CPU virtualization support enabled in firmware, sufficient memory/storage, and suitable networking. On Windows or macOS, develop the control plane locally and use a separate Linux machine or a Linux VM with nested virtualization for realistic hypervisor testing.
