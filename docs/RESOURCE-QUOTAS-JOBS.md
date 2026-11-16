# Resource quotas and durable provisioning worker

Customer accounts support independent VM-count, vCPU, memory (MiB), and storage (GiB) limits. Defaults for new accounts are 3 VMs, 8 vCPU, 16 GiB memory, and 200 GiB storage. Administrators can override limits through the user create/update API. Existing databases are upgraded in place.

## Queue lifecycle

`POST /api/vms` validates the request, reserves the customer's resource allocation, and inserts a `queued` provisioning job in one SQLite `BEGIN IMMEDIATE` transaction. It returns HTTP `202 Accepted` with a `provisioning_job_id`. The frontend polls `GET /api/jobs/{job_id}` and displays the job state. Customers can read only their own jobs; administrators can inspect jobs by ID.

The API process runs a single asynchronous queue consumer. If enrolled nodes exist, it selects a fresh online node with sufficient CPU, RAM, storage and provider compatibility. It transactionally reserves the VM allocation, records the node assignment and enqueues a typed `vm.create` command. The node agent claims the command over its authenticated outbound channel and reports completion. If no nodes are enrolled, the legacy single-agent URL remains supported. Job request, node assignment, attempts, result and error persist in SQLite.

## Crash and failure safety

At startup, commands left `running` are marked failed with an explicit reconciliation warning. Jobs with a still-queued node command remain pending so the node can claim them after the control plane restarts; legacy or in-flight ambiguous jobs are marked failed. The worker does **not** automatically replay an ambiguous in-flight command: a process may have crashed after the provider created the VM but before the result was committed. Replaying such an operation could create a duplicate or conflict with an existing VM. The corresponding ownership/allocation reservation is deliberately retained until an operator reconciles the provider state. This is conservative quota accounting, not automatic cleanup.

Agent errors are recorded as failed and the allocation is retained for the same reason: a timeout or transport failure does not prove that the provider did not perform the operation. Safe automated retries require provider-side idempotency keys and an authoritative VM reconciliation API; neither is implemented yet.

## Deployment constraints

Run exactly one API process/worker while using this SQLite-backed queue. Multi-process worker coordination, command leases, automated retries, cancellation, and operator reconciliation workflows remain future work. Node commands are at-most-once dispatched: ambiguous in-flight outcomes are not blindly replayed. Do not deploy multiple Uvicorn workers against this implementation: each process runs startup recovery and a worker loop. Use a single worker until distributed coordination is added.

Existing VM records migrated from older databases receive conservative baseline allocations (1 vCPU, 512 MiB, 8 GiB) because historical actual allocations are not available in the old schema.


## Node agent command channel

Enrolled agents maintain an outbound HTTPS heartbeat and poll `POST /api/nodes/{node_id}/commands/claim` using their per-node bearer credential. The control plane exposes only a finite command vocabulary (`vm.create`, `vm.start`, `vm.stop`, `vm.delete`, `vm.console`, `network.create`, `network.delete`). Completion is reported to the matching `/complete` endpoint. There is no arbitrary shell/command operation. VM inventory and virtual switches are included in authenticated heartbeat snapshots; command completion refreshes VM and virtual-switch inventory snapshots immediately.

The control plane does not need to initiate a network connection into the node. The agent can remain bound to loopback and only needs outbound HTTPS access to the control plane. Configure `VPS_CONTROL_PLANE_URL`, `VPS_NODE_ID` and `VPS_NODE_TOKEN` on the agent. ISO paths and switch names are interpreted on the selected node and must exist there.


When a customer selects a node-specific virtual switch, placement is constrained to the reporting node and the scheduler verifies the switch name from its latest heartbeat. A switch that disappears between dashboard load and provisioning causes the job to remain queued rather than creating a VM against a different network.


## Virtual network management

Administrators can create and delete node-local networks from the dashboard. Hyper-V supports private isolated switches, with no host access, DHCP or NAT configured by VirtueWise. libvirt supports isolated L2 networks with DHCP and NAT networks with DHCP. libvirt networks require a valid, non-overlapping IPv4 CIDR between /16 and /29; the control plane validates the node/provider, and the agent checks overlap against existing libvirt networks before defining the network. Operators must also avoid overlapping the node's physical LAN, VPN and routed address ranges; VirtueWise does not yet discover every external route. Networks are configured to autostart. Deletion is refused while any VM is attached, and built-in default networks are protected. A missing network record can be explicitly forgotten only after a fresh online-node inventory confirms the switch is absent. Ambiguous provisioning/deletion states remain visible for reconciliation. Customer accounts can attach only networks explicitly assigned to their account; each managed network can be assigned to one customer at a time. VM ownership records retain node/network attachment so unassignment, deletion and ownership transfer are blocked while they would break tenant boundaries. Unmanaged provider networks remain administrator-only. This is not yet a complete tenant firewall, public IP, routing or billing system; host firewall policy and external connectivity still require operator configuration.
