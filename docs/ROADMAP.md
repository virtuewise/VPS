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
- [ ] DHCP/IP allocation and public IP management
- [ ] Browser-based console gateway (noVNC or equivalent)
- [x] SSH public-key injection and Ubuntu cloud-init seed generation (Hyper-V prototype)
- [ ] VM templates and snapshots

## Milestone 5 — Multi-tenant hosting

- [ ] User accounts, roles, and permissions
- [ ] Quotas, usage tracking, and audit events
- [ ] Multi-node scheduler and placement policies
- [ ] Backups, restore, and operational alerts
