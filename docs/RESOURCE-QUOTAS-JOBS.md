# Resource quotas and provisioning records

Customer accounts support independent VM-count, vCPU, memory (MiB), and storage (GiB) limits. Defaults for newly created accounts are 3 VMs, 8 vCPU, 16 GiB memory, and 200 GiB storage. Administrators can override these values through the existing user create/update API. Existing databases are upgraded in place.

VM creation reserves its requested allocation in `vm_ownership` inside a SQLite `BEGIN IMMEDIATE` transaction before calling the configured node agent. This serializes concurrent quota checks and prevents two requests from oversubscribing the same account. Failed agent calls release the allocation and leave a failed provisioning record; successful calls mark the record succeeded.

Every create request receives a durable provisioning record. Retrieve its status with `GET /api/jobs/{job_id}`; administrators can inspect all jobs by ID, while customers can only read their own. The create response includes `provisioning_job_id`.

**Scope note:** provisioning is still a synchronous API request against the single configured agent. The job record is durable execution tracking, not yet a detached worker queue with automatic restart/retry. Multi-node dispatch and asynchronous worker recovery remain future work. Existing VM records migrated from older databases receive conservative baseline allocation values (1 vCPU, 512 MiB, 8 GiB) because historical actual allocations are not available in the old schema.
