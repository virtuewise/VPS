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

## Provider manifest contract (version 1 implemented for built-in and signed worker providers)

Each plugin must declare:

- Unique reverse-DNS identifier and semantic version.
- Supported VirtueWise API version range.
- Plugin type and entry point or service endpoint.
- Required and optional capabilities.
- Configuration schema and secret fields.
- Health/readiness checks and resource limits.
- License, source repository, maintainer, and integrity digest.

A provider must fail closed when its API version or declared capability is unavailable. The v1 provider schema is `agent/provider-manifests/schema.json`; Hyper-V and libvirt manifests are validated at startup. Signed third-party `python-worker` packages are also supported through the administrator-managed registry and isolated runtime described below. Capabilities are enforced by the typed worker adapter; they limit which VirtueWise operations are dispatched, not arbitrary Python behavior inside the sandbox.

## Security requirements

1. Plugins receive only explicitly granted capabilities; no implicit administrator access.
2. Secrets are injected at runtime and never placed in manifests, logs, or source control.
3. Provider operations remain typed and validated. No public arbitrary shell/PowerShell execution.
4. Third-party plugins run out-of-process with restricted OS identities, filesystem access, network egress, CPU, and memory.
5. All state-changing plugin actions are attributable and auditable.
6. Installation requires an administrator action; upgrades and removal must be reversible.
7. Provider packages are signed with Ed25519 and verified against administrator-managed trusted publisher keys.
8. Network integrations use TLS; credentials are rotated and revocable.

## ChatGPT integration

The intended integration is a first-party VirtueWise **Model Context Protocol (MCP) server**. MCP is the tool interface for compatible AI clients; it is not the retired legacy ChatGPT Plugins format.

The initial MCP server is implemented in `mcp-server/` and calls dedicated authenticated `/api/integrations/mcp/*` control-plane routes. It does not access SQLite directly. These endpoints accept an optional global `VPS_MCP_READ_TOKEN` for administrator-level read-only access or administrator-issued per-user scoped tokens. Scoped tokens are hashed at rest, expire, are revocable, and filter VM inventory to the token owner. Customer tokens cannot receive node or host-wide resource scopes. The MCP process defaults to stdio and rejects remote plain HTTP, redirects, unbounded responses, and paths outside the dedicated read-only API. Initial tools include:

- `virtuewise_health`
- `list_nodes`
- `get_node_status`
- `list_virtual_machines`
- `get_vm_details`
- `get_resource_usage`

State-changing tools (create, start, stop, delete, network changes) are a later phase. They require explicit authorization, scoped credentials, destructive-action confirmation, idempotency, and audit records. Tool descriptions must state their effects clearly. No tool should expose arbitrary command execution.

The MCP server is independently installable, uses environment-based configuration, and supports stdio plus optional stateless Streamable HTTP with bearer authentication on a loopback listener. Its SDK v2 integration is tested in-memory with the official MCP Client and mocked control-plane calls. Streamable HTTP transport is implemented for compatible MCP clients behind a TLS reverse proxy, with bearer authentication and DNS-rebinding protections. OAuth authorization flows, automatic ChatGPT account linking, and write tools remain future work. Multi-node inventory is aggregated from authenticated node heartbeat snapshots.

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
3. [x] Build the MCP server with read-only tools.
4. [x] Add provider package install/enable/disable, health checks and audit journal.
5. [x] Add rootless Docker worker isolation, package signing, capability checks and lifecycle management.
6. Add carefully scoped write tools and event-driven extensions.

The provider plugin runtime v1 and read-only MCP server are implemented as documented above. State-changing MCP tools remain disabled.


## Provider runtime v1 implementation status

The repository now includes Ed25519-signed `.vwpkg` packages, administrator-managed trust keys, SHA-256 integrity checks, strict archive validation, a local provider registry, install/enable/disable/rollback commands, API compatibility checks, capability-gated typed worker calls, and a short-lived worker process. Third-party providers are selected with `VPS_PROVIDER=plugin:<id>` only after installation and explicit enablement. Runtime activation re-verifies the package signature against the current trust store.

Untrusted provider code is only enabled through a digest-pinned rootless Docker worker with network disabled, read-only filesystem, dropped capabilities, non-root UID, resource limits, and no-new-privileges. The local subprocess mode is reserved for automated tests. This is container isolation, not a formally verified sandbox; deploy Docker rootless under a dedicated unprivileged service account, keep it patched, and do not mount host sockets/secrets. A separate narrow privileged virtualization broker remains the recommended hardening step before accepting arbitrary external publishers at scale.
