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

The Python structural interface, signed package registry, provider CLI, lifecycle controls, and worker adapter are implemented. Third-party execution is deliberately disabled unless a rootless Docker sandbox image pinned by digest is configured. Local `process` mode is test-only and must not be enabled for untrusted providers.


## Version 1 provider manifests (implemented)

Built-in manifests live in `agent/provider-manifests/`; the JSON Schema is `schema.json` (JSON Schema 2020-12). The agent validates the selected built-in manifest or re-verifies the selected installed provider package during startup before serving requests. Unknown fields are rejected, versions must be stable `MAJOR.MINOR.PATCH`, and the agent refuses a provider whose declared API range does not include its current provider API version (`1.0.0`). The loader limits manifests to 32 KiB and rejects symlink paths.

A manifest declares a reverse-DNS identifier, provider version, provider API range, provider type, runtime class, maintainer, license, and explicit capabilities. The initial allowlist is `inventory.read`, `host.metrics.read`, `vm.create`, `vm.power`, `vm.delete`, and `vm.console`. Unknown and duplicate capabilities fail validation. The typed worker adapter checks the declared capability before dispatching each operation. This limits the VirtueWise operation surface but is not a general Python sandbox; arbitrary provider code must remain inside the configured rootless container boundary.

Built-in providers remain a fixed mapping to bundled Hyper-V/libvirt modules. Third-party packages use the explicit `python-worker` runtime and a signed package entrypoint; manifest fields cannot specify shell commands, executable paths, or network endpoints.

Shared contract tests exercise the five-method provider interface. Provider-specific tests remain necessary for platform behavior, and real-host validation is still required before production claims.


## Signed provider package format v1

A `.vwpkg` is a ZIP archive containing `manifest.json` and the Python source file named by `entrypoint` (for example `provider.py` for `provider:Provider`). Version 1 accepts only self-contained pure-Python `.py` files plus the manifest (no native extensions or package-manager dependency installation), up to 256 files, 64 MiB compressed and 128 MiB expanded. Archive traversal, duplicate names, symlinks and unsupported file types are rejected. Packages are signed with Ed25519 over the raw 32-byte SHA-256 digest of the complete archive. Detached signature metadata is JSON: `{"key_id":"publisher-key-1","signature":"<base64>"}`. The trust store is a JSON object mapping key IDs to base64 raw Ed25519 public keys.

### Build, sign, and install

Run commands from the `agent` directory with the agent virtual environment active. Keep publisher private keys offline; never copy them to a node.

```powershell
.\.venv\Scripts\python.exe provider_sign.py keygen --key-id publisher-2026 --private-key publisher-private.key --public-key publisher-public.key
.\.venv\Scripts\python.exe provider_sign.py build ..\examples\providers\sample sample.vwpkg
.\.venv\Scripts\python.exe provider_sign.py sign sample.vwpkg --key-id publisher-2026 --private-key publisher-private.key --output sample.sig.json
```

Create the administrator-managed trust store `trusted-providers.json` with the public key from keygen:

```json
{"publisher-2026":"BASE64_PUBLIC_KEY_FROM_KEYGEN"}
```

Set the same registry and trust-store paths for the installer and the running node agent:

```powershell
$env:VPS_PROVIDER_REGISTRY = "C:\ProgramData\VirtueWise\providers"
$env:VPS_PROVIDER_TRUST_STORE = "C:\ProgramData\VirtueWise\trusted-providers.json"
```

Transfer the package, detached signature, and public-key trust store to the Linux node. Run lifecycle commands on that node (the signing private key stays on the publisher workstation):

```bash
cd /opt/virtuewise/VPS/agent
export VPS_PROVIDER_REGISTRY=/var/lib/virtuewise/providers
export VPS_PROVIDER_TRUST_STORE=/etc/virtuewise/trusted-providers.json
python3 providerctl.py install sample.vwpkg --signature sample.sig.json --trust-store "$VPS_PROVIDER_TRUST_STORE"
python3 providerctl.py list
python3 providerctl.py enable org.example.provider.sample
python3 providerctl.py check org.example.provider.sample
python3 providerctl.py events
python3 providerctl.py disable org.example.provider.sample
python3 providerctl.py rollback org.example.provider.sample
python3 providerctl.py uninstall org.example.provider.sample
```

The install command verifies the detached signature and API compatibility. Newly installed versions are disabled until an administrator enables them. Upgrades preserve earlier versions; rollback switches to the previous installed version and disables the provider until reviewed and re-enabled. Removing a signing key from the trust store revokes it at the next agent startup/runtime verification. The CLI operates on local node state and must be run by a trusted node administrator. Disabling revokes calls on the next agent operation. Installing a newer version selects it but leaves the provider disabled; after reviewing and enabling the new version, restart the node agent so it loads the new package. Rollback also disables the provider; re-enable and restart after review.

### Enable in the node agent

Configure absolute paths and use a rootless Docker installation. The image must be pinned with an immutable digest (not a floating tag):

```dotenv
VPS_PROVIDER=plugin:org.example.provider.sample
VPS_PROVIDER_REGISTRY=/var/lib/virtuewise/providers
VPS_PROVIDER_TRUST_STORE=/etc/virtuewise/trusted-providers.json
VPS_PROVIDER_SANDBOX=docker
VPS_PROVIDER_SANDBOX_IMAGE=python:3.11-slim@sha256:REPLACE_WITH_VERIFIED_IMAGE_DIGEST
```

Third-party execution currently targets Linux nodes with rootless Docker; the Windows Hyper-V provider remains built in. The worker uses `--network=none`, read-only root filesystem, no Linux capabilities, no-new-privileges, a non-root UID, a 256 MiB memory cap, one CPU, 64 process limit, and a small no-exec temporary filesystem. The package and worker source are mounted read-only. The worker protocol is one request per short-lived process, with typed methods, a bounded request/response and a timeout. The agent refuses third-party provider startup if rootless Docker or a digest-pinned image is unavailable. Local subprocess mode is test-only.

**Security boundary:** rootless Docker substantially constrains a provider but is not a formal proof of isolation against kernel/container-runtime vulnerabilities. Keep Docker patched, run rootless under a dedicated unprivileged account, do not mount host sockets or secrets, and expose only the minimum virtualization APIs through a separate privileged broker in a hardened deployment. Provider code can still execute arbitrary Python inside its sandbox; the manifest/capability layer limits which VirtueWise operations the host agent offers, not what arbitrary code can do inside its container. Hyper-V/libvirt providers remain first-party built-ins and are not routed through this third-party package mechanism.

### API and compatibility

The stable provider API is currently `1.0.0`. A provider must declare an inclusive `provider_api_min` / `provider_api_max` range containing that version. The provider must implement the five structural operations in `interface.py`. Capabilities are checked by the worker adapter before dispatch. The worker accepts only inventory, metrics, create and explicit VM lifecycle operations; arbitrary shell/command methods are not part of the SDK.
