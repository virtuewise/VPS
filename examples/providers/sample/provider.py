"""Minimal provider example. This demo intentionally implements read-only methods."""
class Provider:
    def list_vms(self):
        return []

    def list_switches(self):
        return []

    def host_monitor(self):
        return {"cpu_count": 1, "memory_total_mb": 0, "memory_used_mb": 0}

    def create_vm(self, **kwargs):
        raise NotImplementedError("This sample provider is read-only")

    def vm_action(self, action, name, delete_disks=False):
        raise NotImplementedError("This sample provider is read-only")
