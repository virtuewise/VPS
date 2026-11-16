"""One-shot JSON worker. Run only under a separately restricted OS identity/container."""
from __future__ import annotations
import importlib
import json
import sys
import zipfile
from contextlib import redirect_stdout


def main() -> int:
    package, method, payload = sys.argv[1], sys.argv[2], json.loads(sys.stdin.read())
    sys.path.insert(0, package)  # import only from the verified, signed archive
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        module_name, class_name = manifest["entrypoint"].split(":", 1)
        with redirect_stdout(sys.stderr):
            module = importlib.import_module(module_name)
            provider = getattr(module, class_name)()
        if method == "vm_action" and payload.get("action") not in {"start", "stop", "delete", "console"}:
            raise ValueError("Unsupported VM action")
        operations = {"list_vms": lambda: provider.list_vms(), "list_switches": lambda: provider.list_switches(), "host_monitor": lambda: provider.host_monitor(), "create_vm": lambda: provider.create_vm(**payload), "vm_action": lambda: provider.vm_action(payload["action"], payload["name"], delete_disks=payload.get("delete_disks", False)), "create_network": lambda: provider.create_network(**payload), "delete_network": lambda: provider.delete_network(payload["name"])}
        if method not in operations:
            raise ValueError("Unsupported provider operation")
        with redirect_stdout(sys.stderr):
            result = operations[method]()
        output = json.dumps(result, separators=(",", ":"), allow_nan=False)
        if len(output.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Provider result exceeds 1 MiB")
        sys.stdout.write(output + "\n")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write(type(exc).__name__ + ": provider operation failed\n")
        raise SystemExit(1)
