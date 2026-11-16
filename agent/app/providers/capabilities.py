"""Explicit, versioned capability vocabulary for provider integrations."""
from __future__ import annotations

CAPABILITY_METHODS: dict[str, tuple[str, ...]] = {
    "inventory.read": ("list_vms", "list_switches"),
    "host.metrics.read": ("host_monitor",),
    "vm.create": ("create_vm",),
    "vm.power": ("vm_action",),
    "vm.delete": ("vm_action",),
    "vm.console": ("vm_action",),
}


def validate_capabilities(capabilities: list[str]) -> None:
    if len(capabilities) != len(set(capabilities)):
        raise ValueError("Provider capabilities must not contain duplicates.")
    unknown = sorted(set(capabilities) - CAPABILITY_METHODS.keys())
    if unknown:
        raise ValueError("Provider declares unknown capabilities: " + ", ".join(unknown))
