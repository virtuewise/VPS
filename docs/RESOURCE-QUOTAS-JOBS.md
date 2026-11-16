# Resource quotas and durable provisioning worker

Customer accounts support independent VM-count, vCPU, memory (MiB), and storage (GiB) limits. Defaults for new accounts are 3 VMs, 8 vCPU, 16 GiB memory, and 200 GiB storage. Administrators can override limits through the user create/update API. Existing databases are upgraded in place.

## Queue lifecycle

`POST /api/vms` validates the request, reserves the customer's resource allocation, and inserts a `queued` provisioning job in one SQLite `BEGIN IMMEDIATE` transaction. It returns HTTP `202 Accepted` with a `provisioning_job_id`. The frontend polls `GET /api/jobs/{job_id}` and displays the job state. Customers can read only their own jobs; administrators can inspect jobs by ID.

The API process runs an asynchronous queue consumer. Each consumer claims queued jobs inside `BEGIN IMMEDIATE`, so multiple API worker processes sharing the same local SQLite database cannot claim the same job. If enrolled nodes exist, the consumer selects a fresh online node with sufficient CPU, RAM, storage and provider compatibility. It transactionally records the node assignment and enqueues a typed `vm.create` command. The node agent claims the command over its authenticated outbound channel and reports completion. If no nodes are enrolled, the legacy single-agent URL remains supported. Job request, node assignment, attempts, result and error persist in SQLite.

Legacy single-agent work receives a process-specific 60-second worker lease. Node commands receive an expiring lease sized to three times the agent's reported heartbeat interval (bounded to 3 hours, with a 180-second minimum). The agent reports its currently active command ID in heartbeats; only that command's lease is renewed. A restarted agent has no active command ID and therefore cannot keep an orphaned command alive. Command completion clears the lease.

## Crash and failure safety

At startup, the control plane does not mark every `running` command as failed. It preserves work with an unexpired lease, which allows multiple API worker processes and ordinary API restarts to coexist. A periodic reaper marks only expired node-command leases failed and moves associated VM-create jobs into the administrator reconciliation workflow. Expired legacy single-agent leases are marked `manual_required`; they cannot be verified through enrolled-node inventory. Queued node commands remain queued and can be claimed after the control plane restarts.

The worker does **not** automatically replay an ambiguous in-flight command: a process may have crashed after the provider created the VM but before the result was committed. Replaying such an operation could create a duplicate or conflict with an existing VM. The corresponding ownership/allocation reservation is deliberately retained until an operator reconciles the provider state. This is conservative quota accounting, not automatic cleanup.

Agent errors are recorded as failed and the allocation is retained for the same reason: a timeout or transport failure does not prove that the provider did not perform the operation. Safe automated retries require provider-side idempotency keys and an authoritative VM reconciliation API; neither is implemented yet.

## Deployment constraints

Multiple API worker processes on the **same host** can share this SQLite queue: transactional claims prevent duplicate dispatch, process-specific legacy worker leases and node-command leases protect in-flight work, and startup recovery only reaps expired leases. Keep the SQLite database on a local filesystem; this is not a multi-host distributed queue and must not be placed on an arbitrary network filesystem. A shared external database/queue backend, provider-side idempotency, safe automatic retries, cancellation and full lifecycle reconciliation remain future work. Node commands are at-most-once dispatched: ambiguous in-flight outcomes are not blindly replayed.

Existing VM records migrated from older databases receive conservative baseline allocations (1 vCPU, 512 MiB, 8 GiB) because historical actual allocations are not available in the old schema.


## Node agent command channel

Enrolled agents maintain an outbound HTTPS heartbeat and poll `POST /api/nodes/{node_id}/commands/claim` using their per-node bearer credential. The control plane exposes only a finite command vocabulary (`vm.create`, `vm.start`, `vm.stop`, `vm.delete`, `vm.console`, `network.create`, `network.delete`). Completion is reported to the matching `/complete` endpoint. There is no arbitrary shell/command operation. VM inventory and virtual switches are included in authenticated heartbeat snapshots; command completion refreshes VM and virtual-switch inventory snapshots immediately.

The control plane does not need to initiate a network connection into the node. The agent can remain bound to loopback and only needs outbound HTTPS access to the control plane. Configure `VPS_CONTROL_PLANE_URL`, `VPS_NODE_ID` and `VPS_NODE_TOKEN` on the agent. ISO paths and switch names are interpreted on the selected node and must exist there.


When a customer selects a node-specific virtual switch, placement is constrained to the reporting node and the scheduler verifies the switch name from its latest heartbeat. A switch that disappears between dashboard load and provisioning causes the job to remain queued rather than creating a VM against a different network.


## Virtual network management

Administrators can create and delete node-local networks from the dashboard. Hyper-V supports private isolated switches, with no host access, DHCP or NAT configured by VirtueWise. libvirt supports isolated L2 networks with DHCP and NAT networks with DHCP. libvirt networks require a valid, non-overlapping IPv4 CIDR between /16 and /29; the control plane validates the node/provider, and the agent checks overlap against existing libvirt networks before defining the network. Operators must also avoid overlapping the node's physical LAN, VPN and routed address ranges; VirtueWise does not yet discover every external route. Networks are configured to autostart. Deletion is refused while any VM is attached, and built-in default networks are protected. A missing network record can be explicitly forgotten only after a fresh online-node inventory confirms the switch is absent. Ambiguous provisioning/deletion states remain visible for reconciliation. Customer accounts can attach only networks explicitly assigned to their account; each managed network can be assigned to one customer at a time. VM ownership records retain node/network attachment so unassignment, deletion and ownership transfer are blocked while they would break tenant boundaries. Unmanaged provider networks remain administrator-only. This is not yet a complete tenant firewall, public IP, routing or billing system; host firewall policy and external connectivity still require operator configuration.


## Operator recovery for ambiguous node provisioning

Failed VM-create jobs assigned to an enrolled node appear in the administrator dashboard's **Provisioning recovery** panel. Recovery is deliberately an inventory-verification workflow, not an automatic retry:

1. The control plane requires both a fresh node heartbeat and a separately timestamped VM inventory snapshot no older than 180 seconds. A heartbeat that omits inventory does not refresh the inventory timestamp.
2. **Confirm VM exists** is accepted only when the exact VM name appears in that assigned node's latest inventory. The job is marked successful and its ownership/quota reservation is retained.
3. **Confirm VM absent** is accepted only when that exact VM name is absent from the fresh inventory. The failed job remains in history, while its ownership/quota reservation is released so an operator or customer can submit a new provisioning request.
4. Both actions are administrator-only and audited. The endpoint rechecks node freshness and VM presence inside a serialized database transaction. Neither action sends a provider command.

This first recovery workflow covers node-routed VM creation. Legacy single-agent jobs without an enrolled `node_id` are marked `manual_required` after their worker lease expires and need direct host inspection. Provider-side partial resource cleanup, VM start/stop/delete reconciliation, multi-host queue coordination and safe provider-level retries remain future work. If the assigned node is offline or cannot report a complete fresh inventory, recovery is blocked and the operator must restore node connectivity or investigate the provider directly.


## VM-create idempotency

`POST /api/vms` accepts an optional `Idempotency-Key` header. Keys must be 8-128 characters using letters, digits, `.`, `_`, `:` or `-`. Keys are unique per authenticated user. The control plane stores a SHA-256 fingerprint of the normalized request alongside the durable job:

- Repeating the same key with the same request returns the original provisioning job instead of creating another VM or consuming quota twice.
- Reusing a key with a different request returns HTTP 409.
- The browser generates a UUID key and reuses it if the initial HTTP request fails before an acceptance response. Once the API accepts the job, the key is retired for the next deliberate submission.
- After an ambiguous job is reconciled as absent, a new provisioning attempt must use a new key.

This protects the control-plane API against client retries and lost responses. Provider-level idempotency keys and safe provider retries are still outstanding; the control plane does not blindly retry provider operations.


## VM and network lifecycle command recovery

The administrator dashboard also lists ambiguous node-routed `vm.start`, `vm.stop`, `vm.delete`, `network.create` and `network.delete` commands. Reconciliation is blocked while the agent still owns an unexpired command lease. After the lease expires or the agent reports failure, the control plane requires a fresh heartbeat and a separately timestamped fresh VM or switch inventory:

- Start/stop are marked successful only when the reported VM state matches the requested state. Unknown provider states are not guessed.
- Delete is marked successful only when the VM is absent; its matching ownership record is then released. A still-present VM remains owned and the command is recorded as a mismatch.
- Network creation is marked successful only when the switch exists; its managed-network record is restored or created as active. If absent, a stale provisioning record is removed.
- Network deletion is marked successful only when the switch is absent; if still present, the managed record is restored to active.

All outcomes are audited. Reconciliation does not execute or retry provider commands. VM ownership transfer and repeated VM deletion are blocked while a deletion command remains unresolved. Console launch commands are not inventory-reconcilable and remain visible through their command status endpoint.
