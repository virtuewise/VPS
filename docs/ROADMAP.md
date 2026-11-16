# VirtueWise VPS roadmap

## Milestone 1 â€” Control-plane foundation

- [x] FastAPI service and dashboard scaffold
- [x] Local SQLite metadata
- [x] Docker Compose development setup
- [x] Initial architecture and open-source license

## Milestone 2 â€” First virtualization provider

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

## Milestone 3 â€” Linux provider and secure node enrollment

- [x] Experimental libvirt/KVM provider prototype behind the shared node-agent API
- [ ] Real-host libvirt acceptance tests and provider conformance suite
- [x] One-time node enrollment tokens (hashed at rest, 15-minute expiry, single use)
- [x] Per-node credentials, authenticated heartbeat API, and administrator revocation (agent push loop and rotation remain open)
- [ ] Mutual TLS between control plane and agents
- [x] SQLite-backed asynchronous provisioning queue with durable job state and conservative restart recovery (single API worker; no safe automatic retries yet)
- [x] Node heartbeats and health status
- [x] Resource-aware node placement preview (provisioning dispatch/reservation not yet integrated)

## Milestone 4 â€” Guest networking and usable VPS

- [ ] Isolated virtual networks and firewall policies
- [ ] DHCP/IP allocation and public IP management
- [ ] Browser-based console gateway (noVNC or equivalent)
- [x] SSH public-key injection and Ubuntu cloud-init seed generation (Hyper-V prototype)
- [ ] VM templates and snapshots

## Milestone 5 â€” Multi-tenant hosting

- [x] Initial customer accounts, administrator/customer roles, ownership checks, and VM-count quotas
- [x] Resource-based VM/CPU/memory/storage quotas and transactional allocation reservation
- [ ] Usage metering, billing, and customer self-service password recovery
- [ ] Multi-node scheduler and placement policies
- [ ] Backups, restore, and operational alerts

## Milestone 6 â€” Plugin ecosystem and AI integrations

- [x] Draft plugin architecture, capability security model, and integration phases
- [x] Versioned provider manifest schema, semantic-version compatibility checks, and capability allowlist
- [x] Versioned provider API 1.0.0 contract and developer starter guide
- [x] Shared provider contract/conformance tests and signed sample provider package
- [x] First-party MCP server with six read-only infrastructure inspection tools and a dedicated scoped API credential
- [x] MCP installation and developer integration guide
- [x] Per-customer delegated read-only MCP tokens with ownership filtering, expiry and revocation
- [x] Streamable HTTP MCP transport with bearer authentication and TLS reverse-proxy deployment model
- [ ] OAuth authorization flows and automatic ChatGPT account linking
- [x] Signed Ed25519 provider packages, trust-store verification, SHA-256 integrity and strict archive validation
- [x] Rootless Docker worker profile with typed capability-gated calls and resource restrictions
- [x] Provider install, upgrade, enable, disable, rollback, uninstall, health-check and audit journal commands
- [x] Provider SDK documentation, sample package and searchable developer portal
- [ ] Real rootless-Docker sandbox acceptance testing and independent security review

## Engineering gate â€” before public hosting

- [ ] Run the complete backend and agent test suites on a clean Windows 11 Pro/Enterprise host
- [ ] Validate VM create/start/stop/console/network/delete against real Hyper-V
- [ ] Validate cloud-init on each explicitly supported Ubuntu Server ISO release
- [ ] Add rollback/cleanup for partially completed VM provisioning
- [x] Persist provisioning jobs and recover queued work after API restarts
- [ ] Add provider idempotency keys, safe retries, lifecycle reconciliation and operator recovery workflow
- [ ] Add encrypted backups and tested restore procedures
- [ ] Complete threat modelling, dependency scanning, secret rotation, and external security review
- [ ] Add resource enforcement, abuse controls, acceptable-use process, and operational monitoring
- [ ] Document hardware, bandwidth, electricity, public-IP, and support costs honestly