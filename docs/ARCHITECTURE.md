# Architecture

## Current implementation

- FastAPI serves the dashboard, REST API and searchable developer portal.
- SQLite stores users, quotas, VM ownership, nodes, signed MCP token metadata, durable provisioning jobs and node commands.
- Node agents enroll with single-use tokens, then send authenticated HTTPS heartbeats and VM/switch inventory over outbound connections.
- The control plane performs resource-aware node placement with persisted CPU/RAM/storage reservations.
- VM lifecycle and provider-capability-gated virtual network commands are dispatched over a typed, authenticated node polling channel; the agent does not need an inbound network listener.
- Hyper-V and libvirt are first-party providers; the provider SDK supports signed third-party packages in an isolated worker profile.

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

The control plane never runs arbitrary host commands or mounts the host Docker socket. Node agents expose only typed, allow-listed operations and validate requests before executing them. Same-host multi-process queue coordination now uses SQLite transactional claims and expiring worker/node-command leases. A multi-host distributed queue backend, mutual TLS, external security review and real-host provider certification remain open.

## Development stages

1. Local dashboard and health API.
2. User identity, sessions, roles, and audit events.
3. [x] One-time node enrollment, per-node bearer identity and authenticated heartbeat.
4. [x] Outbound node command channel and VM/switch inventory snapshots.
5. [x] Resource-aware multi-node provisioning and VM lifecycle routing.
6. [ ] Tenant firewall policy, public IPs, snapshots, backups and browser console.
7. [ ] Distributed queue coordination, mTLS and real-host certification.

## Laptop-first development

A standard laptop is enough for API and dashboard development. Actual KVM virtualization requires a compatible Linux host, CPU virtualization support enabled in firmware, sufficient memory/storage, and suitable networking. On Windows or macOS, develop the control plane locally and use a separate Linux machine or a Linux VM with nested virtualization for realistic hypervisor testing.
