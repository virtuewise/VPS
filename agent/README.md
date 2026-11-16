# VirtueWise VPS Windows Node Agent

Early development Hyper-V provider for a local Windows 11 Pro/Enterprise/Education compute node.

See docs/WINDOWS-11-HYPERV.md and docs/HYPERV-ADVANCED-FEATURES.md.

The agent is loopback-only by default. All VM operations require a bearer token and use a fixed allow-list of Hyper-V actions. Do not expose it to a network or the public internet until secure node enrollment, TLS, authorization, and audit logging are implemented.

Cloud-init seed ISO generation additionally requires Windows ADK Deployment Tools (oscdimg.exe), configured through PATH or VPS_OSCDIMG. The console endpoint launches VMConnect on the node's interactive desktop; it does not provide browser streaming.

This is an unvalidated development milestone. Test provisioning, shutdown, console, network reporting and deletion in disposable VMs before storing important data.

## Automatic node heartbeat

After enrolling a node through the control plane, configure `VPS_CONTROL_PLANE_URL`, `VPS_NODE_ID`, and `VPS_NODE_TOKEN` together. The agent then reports host CPU, memory, storage, provider, and version telemetry on the configured interval (`VPS_HEARTBEAT_INTERVAL`, default 60 seconds; minimum 15 seconds). Transient failures retry with exponential backoff and jitter. Remote control-plane URLs must use HTTPS; plain HTTP is allowed only for loopback development. The node token is a separate credential from `VPS_AGENT_TOKEN`. Store it in a protected environment/secret store. Rotating the node credential invalidates the old token immediately; update the node's `VPS_NODE_TOKEN` and restart the agent.
