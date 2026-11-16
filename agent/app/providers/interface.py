"""Provider contract shared by first-party and third-party virtualization drivers.

Providers implement this structural interface. The agent remains responsible for
authentication, request validation, authorization, and audit-safe operation routing.
"""
from __future__ import annotations

from typing import Any, Protocol


class Provider(Protocol):
    name: str

    def list_vms(self) -> list[dict[str, Any]]: ...
    def list_switches(self) -> list[dict[str, Any]]: ...
    def host_monitor(self) -> dict[str, Any]: ...
    def create_vm(
        self, *, name: str, memory_mb: int, disk_gb: int, cpu_count: int,
        iso_path: str, switch_name: str | None = None,
        cloud_init: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    def vm_action(self, action: str, name: str, *, delete_disks: bool = False) -> Any: ...


def validate_provider(provider: Any) -> None:
    """Fail fast when a configured provider does not implement the required API."""
    required = ("list_vms", "list_switches", "host_monitor", "create_vm", "vm_action")
    missing = [method for method in required if not callable(getattr(provider, method, None))]
    if missing:
        raise TypeError("Provider is missing required methods: " + ", ".join(missing))
