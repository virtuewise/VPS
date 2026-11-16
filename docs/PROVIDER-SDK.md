# Provider SDK starter guide

VirtueWise node providers implement the structural contract in `agent/app/providers/interface.py`.

## Required operations

- `list_vms()`: return normalized VM dictionaries.
- `list_switches()`: return normalized network/switch dictionaries.
- `host_monitor()`: return host capacity and utilization metrics.
- `create_vm(...)`: create a VM from validated resource and image inputs.
- `vm_action(action, name, ...)`: perform only explicitly supported lifecycle operations.

## Design rules

- Use typed arguments and return normalized provider-neutral data.
- Validate VM names, image paths, resource limits, and network identifiers.
- Never expose arbitrary shell execution through the provider API.
- Make power operations idempotent where the underlying platform permits.
- Clean up partially-created resources after provisioning failures.
- Keep destructive storage deletion explicit and guarded.
- Do not log credentials, cloud-init secrets, or private keys.
- Include unit tests with mocked platform commands and provider conformance tests.

## Adding a provider

1. Implement the five required operations in a new module under `agent/app/providers/`.
2. Add a provider selection option only after reviewing its security and configuration model.
3. Add provider-specific unit tests and shared conformance tests.
4. Document host prerequisites and unsupported features.
5. Validate against a real host before marking the provider stable.

The current interface is a Python structural contract and startup validation, not yet a third-party package registry, sandbox, or stable external SDK. Those are later plugin-runtime deliverables.


## Version 1 provider manifests (implemented)

Built-in manifests live in `agent/provider-manifests/`; the JSON Schema is `schema.json` (JSON Schema 2020-12). The agent validates the selected built-in manifest during startup before serving requests. Unknown fields are rejected, versions must be stable `MAJOR.MINOR.PATCH`, and the agent refuses a provider whose declared API range does not include its current provider API version (`1.0.0`). The loader limits manifests to 32 KiB and rejects symlink paths.

A manifest declares a reverse-DNS identifier, provider version, provider API range, provider type, runtime class, maintainer, license, and explicit capabilities. The initial allowlist is `inventory.read`, `host.metrics.read`, `vm.create`, `vm.power`, `vm.delete`, and `vm.console`. Unknown and duplicate capabilities fail validation. These declarations are metadata and a security boundary for subsequent runtime work; they do not yet grant access or independently authorize API operations.

Only `runtime: builtin` is accepted. Provider selection remains a fixed mapping to the bundled Hyper-V/libvirt modules; manifest fields cannot specify Python import paths, shell commands, executable paths, or network endpoints. Third-party installation and isolated execution are intentionally not enabled in this milestone.

Shared contract tests exercise the five-method provider interface. Provider-specific tests remain necessary for platform behavior, and real-host validation is still required before production claims.
