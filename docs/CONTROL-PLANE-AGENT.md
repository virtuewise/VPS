# Control plane and multi-node agent setup

VirtueWise supports two execution modes:

- **Enrolled multi-node mode (recommended):** each node initiates outbound HTTPS heartbeats and polls a durable typed command queue. The control plane never opens an inbound connection to the node agent.
- **Legacy single-agent mode:** `VPS_AGENT_URL` and `VPS_AGENT_TOKEN` proxy operations to one configured agent. This remains for local development and migration.

## 1. Enroll a node

An administrator creates a single-use enrollment token from the dashboard under **Administration ? Compute node enrollment**, or through the API. The dashboard displays the raw token once and provides a copy action. Administrators can also revoke nodes from the node-management list. Customer sessions receive only node IDs, names, status and VM counts; hostnames and telemetry are administrator-only. Include the session cookie and CSRF header:

```http
POST /api/nodes/enrollment-tokens
Content-Type: application/json
Cookie: vwps_session=YOUR_SESSION
X-CSRF-Token: YOUR_CSRF_TOKEN

{"name":"compute-01","hostname":"compute-01.example.internal"}
```

The response contains a random enrollment token valid for 15 minutes. Deliver it securely to the node operator; do not commit it or put it in a public issue. The node consumes it once:

```http
POST /api/nodes/enroll
Content-Type: application/json

{"token":"ONE_TIME_ENROLLMENT_TOKEN"}
```

The response contains the node ID and a long-lived node identity token, shown only once. Store it in the node's secret environment or service manager. The database stores only its SHA-256 hash. An administrator can revoke a node or rotate its credential; credential rotation requires updating the node's environment and restarting the agent.

## 2. Configure the node agent

On the node, install the agent dependencies and configure:

```dotenv
VPS_AGENT_TOKEN=GENERATE_A_SEPARATE_LOCAL_AGENT_API_TOKEN
VPS_CONTROL_PLANE_URL=https://control.example.com
VPS_NODE_ID=1
VPS_NODE_TOKEN=THE_ONE_TIME_RETURNED_NODE_TOKEN
VPS_HEARTBEAT_INTERVAL=60
VPS_PROVIDER=hyperv
VPS_AGENT_HOST=127.0.0.1
VPS_AGENT_PORT=8765
```

`VPS_AGENT_TOKEN` protects the local agent API and is distinct from `VPS_NODE_TOKEN`, which authenticates heartbeats and command polling to the control plane. Keep the agent bound to loopback. The node needs outbound HTTPS access to the control plane; no inbound firewall port is required for the node command channel.

Start the agent using the service manager appropriate for the node. On Windows, Hyper-V requires a supported Windows edition and virtualization enabled in firmware. On Linux, the libvirt/KVM provider is experimental until certified on a real host.

## 3. What the agent reports and receives

The agent periodically sends authenticated CPU, memory, storage, provider, VM inventory and virtual-switch snapshots. It also polls:

- `POST /api/nodes/{node_id}/commands/claim`
- `POST /api/nodes/{node_id}/commands/{command_id}/complete`

The command vocabulary is finite and typed: `vm.create`, `vm.start`, `vm.stop`, `vm.delete`, and `vm.console`. There is no arbitrary shell, Python expression or command-execution endpoint. The agent refreshes VM inventory after successful lifecycle commands.

## 4. Scheduling and provisioning

Provisioning jobs are durable. When enrolled nodes exist, the worker selects an online node with a fresh heartbeat, matching provider (when requested), and sufficient CPU, RAM and storage. Existing VM reservations are subtracted from schedulable capacity before dispatch. The node assignment and command are recorded transactionally. The node agent claims and completes the command over its outbound connection.

ISO paths and virtual switch names refer to resources on the selected node. Ensure installation media is present on every eligible node, or request a provider/node placement policy appropriate to your storage layout. If enrolled nodes exist but none are currently eligible, the job remains queued rather than bypassing capacity checks. The legacy single-agent fallback is used only when no enrolled nodes exist.

The dashboard aggregates VM inventory, virtual switches and host monitoring from fresh node heartbeats. Customer inventory is filtered through VM ownership. Node-routed start, stop, delete and console actions use the same command channel.

## 5. Operational limits

- The SQLite-backed queue currently assumes one API process/worker. Do not run multiple Uvicorn workers against the same database.
- Commands are not blindly retried after an ambiguous in-flight outcome. Reconcile the VM on the node before re-issuing an operation.
- Real-host Hyper-V and libvirt certification, mTLS, distributed queue leases, automatic credential rotation, and operator reconciliation workflows remain outstanding.
- Heartbeats older than 180 seconds are not eligible for new placements.
- Resource telemetry must be complete; missing values fail scheduling closed.
