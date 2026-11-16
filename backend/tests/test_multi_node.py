import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from app import main


class MultiNodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        main.DATABASE_PATH = Path(self.temp.name) / "multi-node.db"
        main.AGENT_URL = ""
        main.AGENT_TOKEN = ""
        main.ADMIN_PASSWORD = "multi-node-admin-password-long"
        main.SESSION_SECRET = "multi-node-session-secret-long-enough-123456789"
        self.node_token = "node-command-token-that-is-at-least-32-characters"

    def tearDown(self): self.temp.cleanup()

    def _login(self, client):
        response = client.post("/api/auth/login", json={"username": "admin", "password": main.ADMIN_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-CSRF-Token": response.json()["csrf_token"]}

    def _enroll_live_node(self):
        main.initialize_database()
        with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
            cursor = db.execute("""INSERT INTO nodes(name,hostname,status,token_hash,last_seen,resources_json)
                VALUES(?,?,?,?,?,?)""", (
                "compute-a", "compute-a.local", "pending", hashlib.sha256(self.node_token.encode()).hexdigest(),
                int(time.time()), json.dumps({"cpu_total": 8, "cpu_used": 0.1, "memory_total_mb": 32768,
                    "memory_used_mb": 1024, "storage_total_gb": 500, "storage_used_gb": 20, "provider": "hyperv"}),
            ))
            db.commit()
            return cursor.lastrowid

    def test_node_heartbeat_claims_command_and_completes_multi_node_provisioning(self):
        with TestClient(main.app) as client:
            headers = self._login(client)
            node_id = self._enroll_live_node()
            node_headers = {"Authorization": "Bearer " + self.node_token}
            heartbeat = client.post(f"/api/nodes/{node_id}/heartbeat", headers=node_headers, json={
                "cpu_total": 8, "cpu_used": 0.1, "memory_total_mb": 32768, "memory_used_mb": 1024,
                "storage_total_gb": 500, "storage_used_gb": 20, "provider": "hyperv",
                "inventory": [], "switches": [{"name": "Default Switch"}],
            })
            self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
            invalid_heartbeat = client.post(f"/api/nodes/{node_id}/heartbeat", headers=node_headers, json={
                "cpu_total": 8, "cpu_used": -1, "memory_total_mb": 32768, "memory_used_mb": 1024,
                "storage_total_gb": 500, "storage_used_gb": 20, "provider": "hyperv", "agent_version": "test",
                "inventory": [], "switches": [],
            })
            self.assertEqual(invalid_heartbeat.status_code, 422)
            self.assertEqual(client.post(f"/api/nodes/{node_id}/heartbeat", headers={"Authorization": "Bearer wrong-token"}, json={"provider": "hyperv"}).status_code, 401)

            queued = client.post("/api/vms", headers=headers, json={
                "name": "multi-node-vm", "provider": "hyperv", "iso_path": "C:\\ISO\\ubuntu.iso",
                "memory_mb": 2048, "disk_gb": 30, "cpu_count": 2, "node_id": node_id, "switch_name": "Default Switch",
            })
            self.assertEqual(queued.status_code, 202, queued.text)
            job_id = queued.json()["provisioning_job_id"]
            deadline = time.time() + 5
            command = None
            while time.time() < deadline:
                with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                    command = db.execute("SELECT id,node_id,kind,status,payload_json FROM node_commands WHERE job_id=?", (job_id,)).fetchone()
                if command: break
                time.sleep(0.05)
            self.assertIsNotNone(command, "provisioning worker did not dispatch a node command")
            self.assertEqual(command[1:4], (node_id, "vm.create", "queued"))
            command_payload = json.loads(command[4])
            self.assertNotIn("provider", command_payload)
            self.assertNotIn("node_id", command_payload)
            self.assertEqual(command_payload["switch_name"], "Default Switch")

            claimed = client.post(f"/api/nodes/{node_id}/commands/claim", headers=node_headers)
            self.assertEqual(claimed.status_code, 200, claimed.text)
            self.assertEqual(claimed.json()["command"]["id"], command[0])
            self.assertIsNone(client.post(f"/api/nodes/{node_id}/commands/claim", headers=node_headers).json()["command"])
            completed = client.post(f"/api/nodes/{node_id}/commands/{command[0]}/complete", headers=node_headers, json={
                "status": "succeeded", "result": {"name": "multi-node-vm", "_inventory": [{"name": "multi-node-vm", "state": "Off"}],
                    "_switches": [{"name": "Default Switch", "switch_type": "Internal"}]}
            })
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertEqual(client.post(f"/api/nodes/{node_id}/commands/{command[0]}/complete", headers=node_headers, json={"status": "succeeded", "result": {}}).status_code, 404)

            job = client.get(f"/api/jobs/{job_id}", headers=headers).json()
            self.assertEqual(job["status"], "succeeded", job)
            self.assertEqual(job["node_id"], node_id)
            self.assertEqual(job["result"]["name"], "multi-node-vm")
            inventory = client.get("/api/vms", headers=headers).json()
            self.assertEqual(inventory["items"][0]["name"], "multi-node-vm")
            self.assertEqual(inventory["items"][0]["node_id"], node_id)
            nodes = client.get("/api/nodes", headers=headers).json()
            self.assertEqual(nodes["items"][0]["vm_count"], 1)
            switches = client.get("/api/switches", headers=headers).json()
            self.assertEqual(switches["items"][0]["name"], "Default Switch")
            monitor = client.get("/api/monitor", headers=headers).json()
            self.assertEqual(monitor["nodes_online"], 1)

            # A telemetry refresh that temporarily cannot read inventory must retain the last good snapshot.
            with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                switches_seen_before = db.execute("SELECT switches_seen FROM nodes WHERE id=?", (node_id,)).fetchone()[0]
            partial = client.post(f"/api/nodes/{node_id}/heartbeat", headers=node_headers, json={"provider": "hyperv"})
            self.assertEqual(partial.status_code, 200, partial.text)
            retained = client.get("/api/vms", headers=headers).json()
            self.assertEqual(retained["items"][0]["name"], "multi-node-vm")
            with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                switches_seen_after = db.execute("SELECT switches_seen FROM nodes WHERE id=?", (node_id,)).fetchone()[0]
            self.assertEqual(switches_seen_after, switches_seen_before)

            # The VM's persisted reservation reduces schedulable node capacity.
            blocked = client.post("/api/scheduler/placement", headers=headers, json={
                "cpu_count": 7, "memory_mb": 1024, "disk_gb": 10, "provider": "hyperv"
            })
            self.assertEqual(blocked.status_code, 409, blocked.text)

    def test_network_management_is_admin_only_and_provider_scoped(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.post("/api/networks", json={"node_id": 1, "name": "no-auth", "mode": "isolated"}).status_code, 401)
            admin_headers = self._login(client)
            node_id = self._enroll_live_node()
            node_headers = {"Authorization": "Bearer " + self.node_token}
            heartbeat = client.post(f"/api/nodes/{node_id}/heartbeat", headers=node_headers, json={
                "cpu_total": 8, "cpu_used": 0.1, "memory_total_mb": 32768, "memory_used_mb": 1024,
                "storage_total_gb": 500, "storage_used_gb": 20, "provider": "libvirt", "agent_version": "test",
                "capabilities": ["inventory.read", "network.manage"], "inventory": [], "switches": [],
            })
            self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
            with patch("app.main.dispatch_node_command", new_callable=AsyncMock) as dispatch:
                dispatch.return_value = {"name": "tenant-net", "active": True}
                created = client.post("/api/networks", headers=admin_headers, json={
                    "node_id": node_id, "name": "tenant-net", "mode": "nat", "cidr": "192.168.80.0/24"
                })
                self.assertEqual(created.status_code, 200, created.text)
                dispatch.assert_awaited_once_with(node_id, 1, "network.create", {
                    "name": "tenant-net", "mode": "nat", "cidr": "192.168.80.0/24"
                })
            duplicate = client.post("/api/networks", headers=admin_headers, json={
                "node_id": node_id, "name": "tenant-net", "mode": "nat", "cidr": "192.168.81.0/24"
            })
            self.assertEqual(duplicate.status_code, 409)
            with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                db.execute("UPDATE nodes SET switches_json=? WHERE id=?", (json.dumps([{"name": "tenant-net", "switch_type": "nat"}]), node_id))
                db.commit()
            managed = client.get("/api/networks", headers=admin_headers).json()
            self.assertEqual(managed["items"][0]["status"], "active")
            self.assertTrue(managed["items"][0]["present"])
            self.assertEqual(client.delete(f"/api/networks/{node_id}/unmanaged-net", headers=admin_headers).status_code, 404)
            with patch("app.main.dispatch_node_command", new_callable=AsyncMock) as dispatch:
                dispatch.return_value = {"name": "tenant-net", "deleted": True}
                deleted = client.delete(f"/api/networks/{node_id}/tenant-net", headers=admin_headers)
                self.assertEqual(deleted.status_code, 200, deleted.text)
                dispatch.assert_awaited_once_with(node_id, 1, "network.delete", {"name": "tenant-net"})
            self.assertEqual(client.get("/api/networks", headers=admin_headers).json()["items"], [])
            with patch("app.main.dispatch_node_command", new_callable=AsyncMock) as dispatch:
                dispatch.return_value = {"name": "orphaned-net", "created": True}
                self.assertEqual(client.post("/api/networks", headers=admin_headers, json={
                    "node_id": node_id, "name": "orphaned-net", "mode": "nat", "cidr": "192.168.82.0/24"
                }).status_code, 200)
            self.assertEqual(client.get("/api/networks", headers=admin_headers).json()["items"][0]["status"], "missing")
            forgotten = client.delete(f"/api/networks/{node_id}/orphaned-net", headers=admin_headers)
            self.assertEqual(forgotten.status_code, 200, forgotten.text)
            self.assertTrue(forgotten.json()["forgotten"])
            unsupported = client.post("/api/networks", headers=admin_headers, json={
                "node_id": node_id, "name": "tenant-nat", "mode": "isolated"
            })
            self.assertEqual(unsupported.status_code, 422, unsupported.text)

    def test_signed_plugin_network_capability_is_reported_and_honored(self):
        with TestClient(main.app) as client:
            admin_headers = self._login(client)
            node_id = self._enroll_live_node()
            node_headers = {"Authorization": "Bearer " + self.node_token}
            heartbeat = client.post(f"/api/nodes/{node_id}/heartbeat", headers=node_headers, json={
                "provider": "org.example.virtualization", "agent_version": "test",
                "capabilities": ["inventory.read", "network.manage"],
                "cpu_total": 8, "cpu_used": 0, "memory_total_mb": 32768, "memory_used_mb": 1024,
                "storage_total_gb": 500, "storage_used_gb": 20, "inventory": [], "switches": [],
            })
            self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
            with patch("app.main.dispatch_node_command", new_callable=AsyncMock) as dispatch:
                dispatch.return_value = {"name": "plugin-net", "created": True}
                response = client.post("/api/networks", headers=admin_headers, json={
                    "node_id": node_id, "name": "plugin-net", "mode": "isolated"
                })
            self.assertEqual(response.status_code, 200, response.text)
            dispatch.assert_awaited_once_with(node_id, 1, "network.create", {
                "name": "plugin-net", "mode": "isolated", "cidr": None
            })

    def test_customer_inventory_is_filtered_to_owned_vms_across_nodes(self):
        with TestClient(main.app) as client:
            admin_headers = self._login(client)
            user = client.post("/api/users", headers=admin_headers, json={
                "username": "node-customer", "password": "node-customer-password-123", "role": "user", "vm_quota": 5
            })
            self.assertEqual(user.status_code, 200, user.text)
            user_id = user.json()["id"]
            node_id = self._enroll_live_node()
            node_headers = {"Authorization": "Bearer " + self.node_token}
            client.post(f"/api/nodes/{node_id}/heartbeat", headers=node_headers, json={
                "cpu_total": 8, "cpu_used": 0, "memory_total_mb": 32768, "memory_used_mb": 1024,
                "storage_total_gb": 500, "storage_used_gb": 20, "provider": "hyperv",
                "inventory": [{"name": "owned-vm"}, {"name": "private-vm"}], "switches": [],
            })
            with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                db.execute("INSERT INTO vm_ownership(vm_name,user_id,node_id,cpu_count,memory_mb,disk_gb) VALUES(?,?,?,1,512,8)", ("owned-vm", user_id, node_id))
                db.commit()
            login = client.post("/api/auth/login", json={"username": "node-customer", "password": "node-customer-password-123"})
            self.assertEqual(login.status_code, 200, login.text)
            customer_headers = {"X-CSRF-Token": login.json()["csrf_token"]}
            node_summary = client.get("/api/nodes", headers=customer_headers)
            self.assertEqual(node_summary.status_code, 200, node_summary.text)
            self.assertEqual(client.get("/api/networks", headers=customer_headers).status_code, 403)
            self.assertEqual(set(node_summary.json()["items"][0]), {"id", "name", "status", "vm_count"})
            self.assertEqual(client.get("/api/switches", headers=customer_headers).json()["items"], [])
            blocked_network_vm = client.post("/api/vms", headers=customer_headers, json={
                "name": "customer-vm", "iso_path": "C:\\ISO\\ubuntu.iso", "memory_mb": 1024,
                "disk_gb": 10, "cpu_count": 1, "switch_name": "Default Switch"
            })
            self.assertEqual(blocked_network_vm.status_code, 403, blocked_network_vm.text)
            inventory = client.get("/api/vms", headers=customer_headers)
            self.assertEqual(inventory.status_code, 200, inventory.text)
            self.assertEqual([item["name"] for item in inventory.json()["items"]], ["owned-vm"])


if __name__ == "__main__": unittest.main()
