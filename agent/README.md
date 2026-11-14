# VirtueWise VPS Windows Node Agent

Early development Hyper-V provider for a local Windows 11 Pro/Enterprise/Education compute node.

See [Windows 11 Hyper-V setup](../docs/WINDOWS-11-HYPERV.md) for prerequisites and safe local setup.

The agent is intentionally loopback-only by default. All VM operations require a bearer token and use a fixed allow-list of Hyper-V actions. Do not expose it to a network or the public internet until secure node enrollment, TLS, authorization, and audit logging are implemented.

This agent is a development milestone. The control-plane dashboard integration and end-to-end guest provisioning workflow are still in progress.
