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
- [ ] Validate on a supported Windows 11 Hyper-V host
- [ ] Add lifecycle state reconciliation and audit log
- [ ] Secure multi-node enrollment, TLS, and user authorization

## Milestone 3 — Linux provider and secure node enrollment

- [ ] libvirt/KVM provider
- [ ] One-time node enrollment tokens and key rotation
- [ ] Mutual TLS between control plane and agents
- [ ] Job queue for long-running VM operations
- [ ] Node heartbeats and health status

## Milestone 4 — Guest networking and usable VPS

- [ ] Isolated virtual networks and firewall policies
- [ ] DHCP/IP allocation and network status
- [ ] SSH key injection and cloud-init
- [ ] Console access
- [ ] VM templates and snapshots

## Milestone 5 — Multi-tenant hosting

- [ ] User accounts, roles, and permissions
- [ ] Quotas, usage tracking, and audit events
- [ ] Multi-node scheduler and placement policies
- [ ] Backups, restore, and operational alerts
