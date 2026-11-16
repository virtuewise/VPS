# VirtueWise VPS MCP server

A read-only Model Context Protocol integration for VirtueWise VPS. It exposes six inspection tools: `virtuewise_health`, `list_nodes`, `get_node_status`, `list_virtual_machines`, `get_vm_details`, and `get_resource_usage`.

## Security model

- The MCP process calls only dedicated `/api/integrations/mcp/*` control-plane routes.
- Those routes require a separate `VPS_MCP_READ_TOKEN` of at least 32 characters, checked with constant-time comparison. It is not a node-agent credential or a user session.
- The credential grants infrastructure-wide read-only visibility. Protect it as an administrator secret and give it only to trusted MCP clients.
- The MCP HTTP client rejects non-loopback plain HTTP, does not follow redirects, uses bounded timeouts and response sizes, and never returns the bearer token in tool results.
- This package has no mutation tools. Do not add provisioning or lifecycle actions to this read-only package.
- The default MCP transport is stdio, so the process does not open a network listener.

## Configure

Generate a token with `python -c "import secrets; print(secrets.token_hex(32))"`. Put that value in the control-plane `.env` as `VPS_MCP_READ_TOKEN` (then recreate the API container), and export the exact same value in the MCP process environment. Never commit the real value.

Example:

```powershell
$env:VPS_MCP_READ_TOKEN = "<at-least-32-random-characters>"
$env:VPS_MCP_API_URL = "http://127.0.0.1:8000"
$env:VPS_MCP_TIMEOUT = "10"
```

For a remote control plane, use an HTTPS URL and configure TLS correctly. Never send the credential over public plain HTTP.

Install and run from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
virtuewise-vps-mcp
```

The package pins the official MCP Python SDK v2 (`mcp==2.3.0`) and stdio transport. The v2 API uses `MCPServer` from `mcp.server`; older v1 examples importing `FastMCP` from `mcp.server.fastmcp` are not applicable.

## Tools

| Tool | Purpose |
|---|---|
| `virtuewise_health` | Control-plane and agent health |
| `list_nodes` | Node status and heartbeat/resource telemetry |
| `get_node_status` | One node by numeric ID |
| `list_virtual_machines` | VM inventory from the configured agent |
| `get_vm_details` | Exact-name VM inventory lookup |
| `get_resource_usage` | Host resource telemetry |

This first release intentionally supports one configured node agent for VM inventory. Multi-node VM inventory aggregation, per-customer delegated scopes, OAuth and mutation tools are not implemented.
