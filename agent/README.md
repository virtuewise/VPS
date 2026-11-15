# VirtueWise VPS Windows Node Agent

Early development Hyper-V provider for a local Windows 11 Pro/Enterprise/Education compute node.

See docs/WINDOWS-11-HYPERV.md and docs/HYPERV-ADVANCED-FEATURES.md.

The agent is loopback-only by default. All VM operations require a bearer token and use a fixed allow-list of Hyper-V actions. Do not expose it to a network or the public internet until secure node enrollment, TLS, authorization, and audit logging are implemented.

Cloud-init seed ISO generation additionally requires Windows ADK Deployment Tools (oscdimg.exe), configured through PATH or VPS_OSCDIMG. The console endpoint launches VMConnect on the node's interactive desktop; it does not provide browser streaming.

This is an unvalidated development milestone. Test provisioning, shutdown, console, network reporting and deletion in disposable VMs before storing important data.
