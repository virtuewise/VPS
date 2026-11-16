"""Read VM inventory using the dedicated read-only MCP integration API."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.parse
import urllib.request

base = os.environ["VPS_API_URL"].rstrip("/")
token = os.environ["VPS_MCP_READ_TOKEN"]
parsed = urllib.parse.urlparse(base)
if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("Remote control-plane connections must use HTTPS")
url = base + "/api/integrations/mcp/vms"
request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read(1024 * 1024 + 1))
except urllib.error.HTTPError as exc:
    raise SystemExit(f"Control plane returned HTTP {exc.code}") from exc
if len(json.dumps(payload).encode("utf-8")) > 1024 * 1024:
    raise SystemExit("Response exceeds the example's 1 MiB safety limit")
print(json.dumps(payload, indent=2))
