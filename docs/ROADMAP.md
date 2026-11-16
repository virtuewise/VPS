# VirtueWise VPS roadmap

## Milestone 1 — Control-plane foundation

- [x] FastAPI service and dashboard scaffold
- [x] Local SQLite metadata
- [x] Docker Compose development setup
- [x] Initial architecture and open-source license

## Milestone 2 — First virtualization provider

- [x] Windows Hyper-V node agent scaffold
- [x] Bearer-token protected typed API
- [x] VM create/list/start/stop/delete operations
- [x] Resource bounds for CPU, memory, and disk
- [x] Control-plane proxy endpoints for a configured local agent
- [x] Dashboard agent health and VM inventory
- [x] Dashboard VM start/stop controls
- [x] Dashboard VM creation form with resource bounds, ISO path, and optional virtual switch
- [x] Normalize Hyper-V inventory fields for the dashboard
- [x] Host-launched Hyper-V console action (not browser streaming)
- [x] SSH public-key injection through Ubuntu cloud-init seed media
- [x] Ubuntu Server autoinstall seed generation (requires Windows ADK oscdimg)
- [x] Hyper-V guest IP and virtual NIC inventory
- [x] Exact-name deletion confirmation and opt-in managed-disk deletion
- [x] Host CPU, RAM and VM-volume capacity sampling
- [ ] Validate all operations on a supported Windows 11 Hyper-V host
- [ ] Test cloud-init against supported Ubuntu Server ISO versions
- [x] Single-administrator login, signed expiring sessions and CSRF protection for control-plane APIs
- [x] Bind Docker development port to localhost by default
- [x] Add administrative audit events for authentication, accounts, ownership, and VM lifecycle requests
- [ ] Add lifecycle state reconciliation and durable operation history
- [x] Add initial multi-user RBAC, VM ownership, VM-count quotas, login throttling, and administrative audit view
- [ ] Secure node enrollment, mutual TLS, token rotation, and production-grade rate limiting

## Milestone 3 — Linux provider and secure node enrollment

- [x] Experimental libvirt/KVM provider prototype behind the shared node-agent API
- [ ] Real-host libvirt acceptance tests and provider conformance suite
- [x] One-time node enrollment tokens (hashed at rest, 15-minute expiry, single use)
- [x] Per-node credentials, authenticated heartbeat API, and administrator revocation (agent push loop and rotation remain open)
- [ ] Mutual TLS between control plane and agents
- [ ] Job queue for long-running VM operations
- [ ] Node heartbeats and health status
- [ ] Resource-aware multi-node scheduler

## Milestone 4 — Guest networking and usable VPS

- [ ] Isolated virtual networks and firewall policies
- [ ] DHCP/IP allocation and public IP management
- [ ] Browser-based console gateway (noVNC or equivalent)
- [x] SSH public-key injection and Ubuntu cloud-init seed generation (Hyper-V prototype)
- [ ] VM templates and snapshots

## Milestone 5 — Multi-tenant hosting

- [x] Initial customer accounts, administrator/customer roles, ownership checks, and VM-count quotas
- [ ] Resource-based quotas, usage metering, billing, and customer self-service password recovery
- [ ] Multi-node scheduler and placement policies
- [ ] Backups, restore, and operational alerts

## Milestone 6 — Plugin ecosystem and AI integrations

- [x] Draft plugin architecture, capability security model, and integration phases
- [ ] Versioned plugin manifest schema and capability registry
- [x] Initial provider interface contract and developer starter guide
- [ ] External provider SDK packaging and conformance suite
- [ ] First-party MCP server for compatible AI clients (read-only tools first)
- [ ] ChatGPT integration guide, OAuth/scoped authorization, and deployment packaging
- [ ] Isolated plugin runtime, signing, health checks, and lifecycle management
- [ ] Plugin SDK, sample plugins, and developer documentation

## Engineering gate — before public hosting

- [ ] Run the complete backend and agent test suites on a clean Windows 11 Pro/Enterprise host
- [ ] Validate VM create/start/stop/console/network/delete against real Hyper-V
- [ ] Validate cloud-init on each explicitly supported Ubuntu Server ISO release
- [ ] Add rollback/cleanup for partially completed VM provisioning
- [ ] Add durable jobs, retries, idempotency keys, and lifecycle reconciliation
- [ ] Add encrypted backups and tested restore procedures
- [ ] Complete threat modelling, dependency scanning, secret rotation, and external security review
- [ ] Add resource enforcement, abuse controls, acceptable-use process, and operational monitoring
- [ ] Document hardware, bandwidth, electricity, public-IP, and support costs honestly
