import os
import unittest
from unittest.mock import patch

os.environ["VPS_AGENT_TOKEN"] = "agent-test-token-that-is-at-least-32-characters"

from fastapi.testclient import TestClient
from app.main import app


class AgentApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + os.environ["VPS_AGENT_TOKEN"]}

    def test_health_is_public_but_vm_inventory_requires_token(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/v1/vms").status_code, 401)
        with patch("app.main.hyperv.list_vms", return_value=[]):
            inventory = self.client.get("/v1/vms", headers=self.headers)
        self.assertEqual(inventory.status_code, 200, inventory.text)
        self.assertEqual(inventory.json(), {"items": []})

    def test_cloud_init_rejects_incomplete_or_unsafe_credentials(self):
        response = self.client.post("/v1/vms", headers=self.headers, json={
            "name": "test-vm", "iso_path": "C:\\ISO\\ubuntu.iso",
            "cloud_init": {"username": "Bad User", "password_hash": "plaintext",
                           "ssh_public_key": "not-a-key"}
        })
        self.assertEqual(response.status_code, 422, response.text)

    def test_delete_requires_exact_url_and_body_confirmation(self):
        payload = {"name": "test-vm", "confirm_name": "different-name", "delete_disks": False}
        response = self.client.request("DELETE", "/v1/vms/test-vm", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 400, response.text)

    def test_delete_delegates_only_after_confirmation(self):
        with patch("app.main.hyperv.vm_action", return_value={"deleted": True}) as action:
            response = self.client.request("DELETE", "/v1/vms/test-vm", headers=self.headers,
                json={"name": "test-vm", "confirm_name": "test-vm", "delete_disks": False})
        self.assertEqual(response.status_code, 200, response.text)
        action.assert_called_once_with("delete", "test-vm", delete_disks=False)


if __name__ == "__main__":
    unittest.main()
