# Read-only API example

`read_only_inventory.py` demonstrates a minimal integration using the dedicated `VPS_MCP_READ_TOKEN`. Set `VPS_API_URL` and `VPS_MCP_READ_TOKEN` in the environment, then run `python read_only_inventory.py`. Use HTTPS for remote control planes. The token grants infrastructure-wide read-only access in the current release; protect it like an administrator credential. Never put it in a browser bundle or source control.
