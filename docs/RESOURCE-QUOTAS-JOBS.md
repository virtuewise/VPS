# Resource quotas and durable provisioning worker

Customer accounts support independent VM-count, vCPU, memory (MiB), and storage (GiB) limits. Defaults for new accounts are 3 VMs, 8 vCPU, 16 GiB memory, and 200 GiB storage. Administrators can override limits through the user create/update API. Existing databases are upgraded in place.

## Queue lifecycle

`POST /api/vms` validates the request, reserves the customer's resource allocation, and inserts a `queued` provisioning job in one SQLite `BEGIN IMMEDIATE` transaction. It returns HTTP `202 Accepted` with a `provisioning_job_id`. The frontend polls `GET /api/jobs/{job_id}` and displays the job state. Customers can read only their own jobs; administrators can inspect jobs by ID.

The API process runs a single asynchronous queue consumer. It claims one queued job transactionally (`queued` → `running`), dispatches the typed VM-create request to the configured node agent, and persists `succeeded` plus the result or `failed` plus a bounded error. The job record, request, attempts and status survive process restarts. The queue currently uses SQLite and the existing single configured agent; it does not yet dispatch to the selected scheduler node.

## Crash and failure safety

At startup, any job left `running` is changed to `failed` with an explicit reconciliation warning. The worker does **not** automatically replay it: a process may have crashed after the provider created the VM but before the result was committed. Replaying such an operation could create a duplicate or conflict with an existing VM. The corresponding ownership/allocation reservation is deliberately retained until an operator reconciles the provider state. This is conservative quota accounting, not automatic cleanup.

Agent errors are recorded as failed and the allocation is retained for the same reason: a timeout or transport failure does not prove that the provider did not perform the operation. Safe automated retries require provider-side idempotency keys and an authoritative VM reconciliation API; neither is implemented yet.

## Deployment constraints

Run exactly one API process/worker while using this SQLite-backed queue. Multi-process worker coordination, leases/heartbeats, a dedicated broker, multi-node routing, automated retries, cancellation, and operator reconciliation endpoints are future work. Do not deploy multiple Uvicorn workers against this implementation: each process runs startup recovery and a worker loop. Use a single worker until distributed coordination is added.

Existing VM records migrated from older databases receive conservative baseline allocations (1 vCPU, 512 MiB, 8 GiB) because historical actual allocations are not available in the old schema.
