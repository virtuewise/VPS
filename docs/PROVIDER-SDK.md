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
