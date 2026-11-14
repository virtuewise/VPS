# Control plane ↔ Hyper-V agent (development)

The control plane can now proxy VM inventory and lifecycle requests to one configured Hyper-V agent. This is a **single-node development integration**, not production node enrollment.

## Safest Windows 11 setup

For the current agent, keep it bound to `127.0.0.1:8765`. Do not expose it to your LAN or the internet.

Because Docker Desktop cannot reach a host service that only listens on the host loopback interface, run the control-plane API directly on Windows for this local-only integration:

1. Start the agent in one PowerShell window using [the agent setup](../docs/WINDOWS-11-HYPERV.md).
2. Copy the generated `VPS_AGENT_TOKEN` printed by the agent.
3. In a second PowerShell window, from the repository root:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:VPS_AGENT_URL = "http://127.0.0.1:8765"
$env:VPS_AGENT_TOKEN = "PASTE-THE-TOKEN-FROM-THE-AGENT-WINDOW"
uvicorn app.main:app --app-dir . --host 127.0.0.1 --port 8000
```

4. Open `http://127.0.0.1:8000`. The dashboard should show agent status and VM inventory.

The agent token is a secret. Do not commit it, put it in frontend JavaScript, or paste it into an issue. Stop both local processes when finished.

## Docker Compose

The Compose file accepts `VPS_AGENT_URL` and `VPS_AGENT_TOKEN`, but the default loopback-only agent cannot be reached from inside the container. Do not change the agent bind address to `0.0.0.0` as a workaround. A safe container-to-host bridge and authenticated node enrollment are future work.

## API proxy

- `GET /api/agent/status` — reports whether the configured agent is reachable
- `GET /api/vms` — lists VMs
- `GET /api/switches` — lists Hyper-V virtual switches
- `POST /api/vms` — creates a VM using the agent's validated request schema
- `POST /api/vms/{name}/start` and `/stop` — controls a VM
- `DELETE /api/vms/{name}` — deletes a VM

The API permits plain HTTP only for loopback addresses. Remote agent connections require HTTPS. This integration has no user login or role system yet; keep the control plane bound to loopback and never expose it publicly.
