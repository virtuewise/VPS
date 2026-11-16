import unittest
import asyncio
from unittest.mock import patch
from app.heartbeat import heartbeat_config, heartbeat_payload, execute_node_command, node_command_loop


class HeartbeatTests(unittest.TestCase):
    def test_disabled_when_unconfigured(self):
        self.assertIsNone(heartbeat_config({}))

    def test_requires_complete_identity(self):
        with self.assertRaises(RuntimeError):
            heartbeat_config({"VPS_CONTROL_PLANE_URL": "https://control.example"})

    def test_rejects_remote_plain_http(self):
        with self.assertRaises(RuntimeError):
            heartbeat_config({"VPS_CONTROL_PLANE_URL": "http://control.example", "VPS_NODE_ID": "1", "VPS_NODE_TOKEN": "x" * 48})

    def test_accepts_loopback_development(self):
        config = heartbeat_config({"VPS_CONTROL_PLANE_URL": "http://127.0.0.1:8000", "VPS_NODE_ID": "2", "VPS_NODE_TOKEN": "x" * 48})
        self.assertEqual(config[1], 2)
        self.assertEqual(config[3], 60)

    def test_payload_includes_vm_and_switch_inventory_snapshots(self):
        payload = heartbeat_payload(
            {"memory_total_mb": 4096, "memory_free_mb": 2048, "storage_total_gb": 100, "storage_free_gb": 50, "cpu_percent": 10},
            "hyperv", "0.4.0", [{"name": "demo"}], [{"name": "Default Switch"}], ["inventory.read", "network.manage"],
        )
        self.assertEqual(payload["inventory"], [{"name": "demo"}])
        self.assertEqual(payload["switches"], [{"name": "Default Switch"}])
        self.assertIn("network.manage", payload["capabilities"])
        active = heartbeat_payload({"memory_total_mb": 1, "memory_free_mb": 1}, "hyperv", "0.4.0",
                                   active_command_id="12345678-1234-1234-1234-123456789abc",
                                   heartbeat_interval_seconds=300)
        self.assertEqual(active["active_command_id"], "12345678-1234-1234-1234-123456789abc")
        self.assertEqual(active["heartbeat_interval_seconds"], 300)

    def test_executes_only_typed_vm_commands_and_refreshes_inventory(self):
        class Provider:
            def create_vm(self, **kwargs): return {"created": kwargs["name"]}
            def vm_action(self, action, name, delete_disks=False): return {"action": action, "name": name}
            def list_vms(self): return [{"name": "demo"}]
        provider = Provider()
        result = asyncio.run(execute_node_command(provider, "vm.create", {
            "name": "demo", "memory_mb": 1024, "disk_gb": 10, "cpu_count": 1, "iso_path": "/tmp/iso"
        }))
        self.assertEqual(result["created"], "demo")
        self.assertEqual(result["_inventory"], [{"name": "demo"}])
        self.assertEqual(asyncio.run(execute_node_command(provider, "vm.start", {"name": "demo"}))["action"], "start")

    def test_node_command_loop_reports_successful_provider_result(self):
        class Provider:
            def create_vm(self, **kwargs): return {"created": kwargs["name"]}
            def list_vms(self): return [{"name": "demo"}]
            def list_switches(self): return []
        class Response:
            def __init__(self, payload): self.payload = payload
            def raise_for_status(self): return None
            def json(self): return self.payload
        class Client:
            completion = None
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def post(self, url, **kwargs):
                if url.endswith("/claim"):
                    return Response({"command": {"id": "command-1", "kind": "vm.create", "payload": {
                        "name": "demo", "memory_mb": 1024, "disk_gb": 10, "cpu_count": 1, "iso_path": "/tmp/iso"
                    }}})
                Client.completion = kwargs["json"]
                raise asyncio.CancelledError()
        command_state = {"active_command_id": None}
        with patch("app.heartbeat.httpx.AsyncClient", return_value=Client()):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(node_command_loop(("http://127.0.0.1:8000", 1, "x" * 40, 60), Provider(), command_state))
        self.assertIsNone(command_state["active_command_id"])
        self.assertEqual(Client.completion["status"], "succeeded")
        self.assertEqual(Client.completion["result"]["_inventory"], [{"name": "demo"}])

    def test_executes_typed_network_commands_and_refreshes_switch_inventory(self):
        class Provider:
            def create_network(self, **kwargs): return {"name": kwargs["name"], "active": True}
            def delete_network(self, name): return {"name": name, "deleted": True}
            def list_vms(self): return []
            def list_switches(self): return [{"name": "tenant-net", "switch_type": "isolated"}]
        provider = Provider()
        created = asyncio.run(execute_node_command(provider, "network.create", {
            "name": "tenant-net", "mode": "isolated", "cidr": None
        }))
        self.assertEqual(created["name"], "tenant-net")
        self.assertEqual(created["_switches"][0]["name"], "tenant-net")
        deleted = asyncio.run(execute_node_command(provider, "network.delete", {"name": "tenant-net"}))
        self.assertTrue(deleted["deleted"])

    def test_rejects_untyped_or_unsupported_network_commands(self):
        class Provider:
            def list_vms(self): return []
            def list_switches(self): return []
        with self.assertRaisesRegex(ValueError, "mode"):
            asyncio.run(execute_node_command(Provider(), "network.create", {
                "name": "tenant-net", "mode": "external", "cidr": None
            }))
        with self.assertRaisesRegex(ValueError, "does not support"):
            asyncio.run(execute_node_command(Provider(), "network.delete", {"name": "tenant-net"}))

    def test_inventory_refresh_failure_does_not_turn_successful_operation_into_failure(self):
        class Provider:
            def create_vm(self, **kwargs): return {"created": kwargs["name"]}
            def list_vms(self): raise RuntimeError("temporary inventory failure")
        result = asyncio.run(execute_node_command(Provider(), "vm.create", {
            "name": "demo", "memory_mb": 1024, "disk_gb": 10, "cpu_count": 1, "iso_path": "/tmp/iso"
        }))
        self.assertEqual(result["created"], "demo")
        self.assertNotIn("_inventory", result)

    def test_rejects_invalid_create_limits_and_non_boolean_delete_flag(self):
        class Provider:
            def create_vm(self, **kwargs): return {}
            def vm_action(self, *args, **kwargs): return {}
            def list_vms(self): return []
        with self.assertRaisesRegex(ValueError, "memory_mb"):
            asyncio.run(execute_node_command(Provider(), "vm.create", {
                "name": "demo", "memory_mb": True, "disk_gb": 10, "cpu_count": 1, "iso_path": "/tmp/iso"
            }))
        with self.assertRaisesRegex(ValueError, "boolean"):
            asyncio.run(execute_node_command(Provider(), "vm.delete", {
                "name": "demo", "confirm_name": "demo", "delete_disks": "yes"
            }))

    def test_rejects_arbitrary_node_commands_and_unconfirmed_deletes(self):
        class Provider:
            def vm_action(self, *args, **kwargs): return {}
            def list_vms(self): return []
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            asyncio.run(execute_node_command(Provider(), "shell.exec", {"command": "whoami"}))
        with self.assertRaisesRegex(ValueError, "confirmation"):
            asyncio.run(execute_node_command(Provider(), "vm.delete", {"name": "demo", "confirm_name": "other"}))

    def test_payload_normalizes_resource_usage(self):
        payload = heartbeat_payload({"cpu_percent": 50, "memory_total_mb": 1000, "memory_free_mb": 250, "storage_total_gb": 100, "storage_free_gb": 40}, "libvirt", "0.3.0")
        self.assertEqual(payload["memory_used_mb"], 750)
        self.assertEqual(payload["storage_used_gb"], 60)
        self.assertEqual(payload["provider"], "libvirt")


if __name__ == "__main__":
    unittest.main()
