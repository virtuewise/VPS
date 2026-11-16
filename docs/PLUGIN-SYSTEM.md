# VirtueWise VPS plugin and integration architecture

## Purpose

VirtueWise VPS will expose a stable, provider-neutral platform API and a permissioned extension system. Integrations must use documented APIs rather than reaching into internal databases or executing arbitrary host commands.

## Plugin model

A plugin is an independently versioned package with a manifest, declared capabilities, configuration schema, health check, and lifecycle hooks. Initial plugin classes:

- **Provider plugins:** implement node/VM operations for virtualization backends.
- **Network plugins:** integrate address allocation, DNS, firewalls, and overlays.
- **Storage plugins:** provide volumes, snapshots, backups, and object storage.
- **Observability plugins:** export metrics, logs, and alerts.
- **Identity/billing plugins:** connect external identity and metering systems.
- **Automation plugins:** expose narrowly scoped workflows and event handlers.

## Manifest contract (draft)

Each plugin must declare:

- Unique reverse-DNS identifier and semantic version.
- Supported VirtueWise API version range.
- Plugin type and entry point or service endpoint.
- Required and optional capabilities.
- Configuration schema and secret fields.
- Health/readiness checks and resource limits.
- License, source repository, maintainer, and integrity digest.

A plugin must fail closed when its API version or required capability is unavailable.

## Security requirements

1. Plugins receive only explicitly granted capabilities; no implicit administrator access.
2. Secrets are injected at runtime and never placed in manifests, logs, or source control.
3. Provider operations remain typed and validated. No public arbitrary shell/PowerShell execution.
4. Third-party plugins run out-of-process with restricted OS identities, filesystem access, network egress, CPU, and memory.
5. All state-changing plugin actions are attributable and auditable.
6. Installation requires an administrator action; upgrades and removal must be reversible.
7. Production plugin packages should be signed and verified against trusted publisher keys.
8. Network integrations use TLS; credentials are rotated and revocable.

## ChatGPT integration

The intended integration is a first-party VirtueWise **Model Context Protocol (MCP) server**. MCP is the tool interface for compatible AI clients; it is not the retired legacy ChatGPT Plugins format.

The MCP server will call the same authenticated public control-plane API used by the dashboard. It must not access SQLite directly or bypass authorization. Initial read-only tools should include:

- `virtuewise_health`
- `list_nodes`
- `get_node_status`
- `list_virtual_machines`
- `get_vm_details`
- `get_resource_usage`

State-changing tools (create, start, stop, delete, network changes) are a later phase. They require explicit authorization, scoped credentials, destructive-action confirmation, idempotency, and audit records. Tool descriptions must state their effects clearly. No tool should expose arbitrary command execution.

The MCP server should be independently installable, use environment-based configuration, support stdio for local clients and a separately secured HTTP transport where supported, and include integration tests with a mocked control plane.

## Compatibility and release policy

- Public API and plugin contracts are versioned.
- Breaking changes require a major version and migration notes.
- Provider conformance tests are mandatory for provider plugins.
- Plugins declare supported platform and provider versions.
- The core remains usable without optional plugins.
- Core dependencies are kept minimal; optional integrations belong in extras.

## Delivery phases

1. Publish API conventions, plugin manifest schema, and capability vocabulary.
2. Extract provider interface and add conformance tests.
3. Build the MCP server with read-only tools.
4. Add plugin discovery, install/enable/disable, and health reporting.
5. Add isolated plugin execution, signing, permissions, and lifecycle management.
6. Add carefully scoped write tools and event-driven extensions.

This document is a design contract, not a claim that the plugin runtime or MCP server has already been implemented.
