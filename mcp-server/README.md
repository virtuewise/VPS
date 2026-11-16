# VirtueWise VPS MCP server

A read-only Model Context Protocol integration for VirtueWise VPS. It exposes six inspection tools: `virtuewise_health`, `list_nodes`, `get_node_status`, `list_virtual_machines`, `get_vm_details`, and `get_resource_usage`.

## Security model

- The MCP process calls only dedicated `/api/integrations/mcp/*` control-plane routes.
- Those routes accept either the optional global `VPS_MCP_READ_TOKEN` (administrator-level read-only access) or a per-user scoped token issued by an administrator. Tokens are never node-agent credentials or browser sessions.
- Scoped tokens are stored as SHA-256 hashes, are bound to an active user, have explicit read scopes and expiry, and can be revoked by an administrator. Raw tokens are returned only once.
- Customer tokens can receive `health.read` and `vms.read`; VM inventory and exact-name detail are filtered to that user's recorded ownership. `nodes.read` and `resources.read` are administrator-only scopes. Global admin token grants all four scopes.
- The MCP HTTP client rejects non-loopback plain HTTP, does not follow redirects, uses bounded timeouts and response sizes, and never returns the bearer token in tool results.
- This package has no mutation tools. Do not add provisioning or lifecycle actions to this read-only package.
- stdio remains the default transport. Optional Streamable HTTP mode is available for remote MCP clients, but binds to loopback only and requires a TLS-terminating reverse proxy for remote access. It uses the same scoped bearer token as the control-plane API.

## Configure

For a global administrator integration, generate a token with `python -c "import secrets; print(secrets.token_hex(32))"`, put it in the control-plane `.env` as `VPS_MCP_READ_TOKEN` (then recreate the API container), and export the same value in the MCP process environment. For customer integrations, an administrator creates a scoped token using `POST /api/mcp/tokens` with an authenticated admin session and CSRF header, for example `{"user_id":42,"label":"Customer assistant","scopes":["health.read","vms.read"],"expires_in_days":30}`. The response contains the token once. Use that token only in the dedicated customer MCP process. Administrators can review `GET /api/mcp/tokens` and revoke with `POST /api/mcp/tokens/{id}/revoke`. Never commit any real token.

Example:

```powershell
$env:VPS_MCP_READ_TOKEN = "<at-least-32-random-characters>"
$env:VPS_MCP_API_URL = "http://127.0.0.1:8000"
$env:VPS_MCP_TIMEOUT = "10"
```

For a remote control plane, use an HTTPS URL and configure TLS correctly. Never send the credential over public plain HTTP.

### Optional Streamable HTTP transport

For a local or reverse-proxied MCP endpoint, configure:

```dotenv
VPS_MCP_TRANSPORT=streamable-http
VPS_MCP_HOST=127.0.0.1
VPS_MCP_PORT=8766
VPS_MCP_PATH=/mcp
VPS_MCP_ALLOWED_HOSTS=127.0.0.1:8766,localhost:8766,mcp.example.com
VPS_MCP_ALLOWED_ORIGINS=https://mcp.example.com
```

Run the same `virtuewise-vps-mcp` command. The HTTP server requires the same `VPS_MCP_READ_TOKEN` (global or per-customer scoped token) in the incoming `Authorization: Bearer` header and when calling the control plane. It rejects unauthenticated requests, non-loopback bind addresses, unapproved Host/Origin values and oversized request bodies. It uses stateless Streamable HTTP at `/mcp`. Expose it remotely only through a TLS reverse proxy bound to the public interface and proxy to `127.0.0.1:8766`; preserve the Authorization header, configure the external host/origin allowlists, and do not expose the local listener directly. OAuth authorization and automatic ChatGPT account linking are not implemented yet.

Install and run from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
virtuewise-vps-mcp
```

The package pins the official MCP Python SDK v2 (`mcp==2.3.0`) and supports stdio plus optional Streamable HTTP transport. The v2 API uses `MCPServer` from `mcp.server`; older v1 examples importing `FastMCP` from `mcp.server.fastmcp` are not applicable.

## Tools

| Tool | Purpose |
|---|---|
| `virtuewise_health` | Control-plane and agent health |
| `list_nodes` | Node status and heartbeat/resource telemetry (admin scope required) |
| `get_node_status` | One node by numeric ID (admin scope required) |
| `list_virtual_machines` | VM inventory from the configured agent |
| `get_vm_details` | Exact-name VM inventory lookup |
| `get_resource_usage` | Host resource telemetry (admin scope required) |

This release intentionally supports one configured node agent for VM inventory. Per-customer read-only tokens and ownership filtering are implemented. OAuth authorization flows, automatic ChatGPT account linking, multi-node VM inventory aggregation and mutation tools remain future work. Streamable HTTP transport is available behind a TLS reverse proxy.
