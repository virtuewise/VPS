import os
import tempfile
import sqlite3
import unittest
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "virtuewise-vps-api-tests.db"
TEST_DB.unlink(missing_ok=True)
os.environ["VPS_ADMIN_PASSWORD"] = "admin-test-password-123456"
os.environ["VPS_SESSION_SECRET"] = "test-session-secret-which-is-long-enough-123456789"
os.environ["VPS_DATABASE_PATH"] = str(TEST_DB)

from fastapi.testclient import TestClient
from app.main import app, CloudInitRequest


class AccountApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        TEST_DB.unlink(missing_ok=True)


    def test_node_enrollment_tokens_are_hashed_single_use_and_expire(self):
        login = self.client.post("/api/auth/login", json={
            "username": "admin", "password": os.environ["VPS_ADMIN_PASSWORD"]
        })
        self.assertEqual(login.status_code, 200)
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        issued = self.client.post("/api/nodes/enrollment-tokens", headers=headers, json={
            "name": "edge-one", "hostname": "edge-one.example.test"
        })
        self.assertEqual(issued.status_code, 200, issued.text)
        token = issued.json()["token"]
        self.assertEqual(issued.json()["expires_in_seconds"], 900)

        import hashlib
        from contextlib import closing
        with closing(sqlite3.connect(TEST_DB)) as db:
            stored = db.execute("SELECT token_hash FROM node_enrollment_tokens").fetchone()[0]
        self.assertNotEqual(stored, token)
        self.assertEqual(stored, hashlib.sha256(token.encode()).hexdigest())

        enrolled = self.client.post("/api/nodes/enroll", json={"token": token})
        self.assertEqual(enrolled.status_code, 200, enrolled.text)
        self.assertTrue(enrolled.json()["registered"])
        replay = self.client.post("/api/nodes/enroll", json={"token": token})
        self.assertEqual(replay.status_code, 401)
        nodes = self.client.get("/api/nodes")
        self.assertEqual(nodes.status_code, 200)
        self.assertTrue(any(item["name"] == "edge-one" for item in nodes.json()["items"]))

    def test_cloud_init_schema_accepts_valid_sha512_hash_and_ssh_key(self):
        request = CloudInitRequest.model_validate({
            "username": "ubuntu",
            "password_hash": "$6$rounds=5000$salt1234$" + "A" * 86,
            "ssh_public_key": "ssh-ed25519 " + "A" * 44 + " test-key",
        })
        self.assertEqual(request.username, "ubuntu")

    def test_account_roles_quotas_suspension_and_audit(self):
        client = self.client
        login = client.post("/api/auth/login", json={
            "username": "admin", "password": os.environ["VPS_ADMIN_PASSWORD"]
        })
        self.assertEqual(login.status_code, 200, login.text)
        admin_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

        for _ in range(5):
            failed = client.post("/api/auth/login", json={"username": "unknown-user", "password": "incorrect"})
            self.assertEqual(failed.status_code, 401)
        limited = client.post("/api/auth/login", json={"username": "unknown-user", "password": "incorrect"})
        self.assertEqual(limited.status_code, 429)

        no_csrf = client.post("/api/users", json={
            "username": "blocked", "password": "long-password-123456", "role": "user", "vm_quota": 1
        })
        self.assertEqual(no_csrf.status_code, 403)

        created = client.post("/api/users", headers=admin_headers, json={
            "username": "customer1", "password": "customer-password-123456",
            "role": "user", "vm_quota": 0
        })
        self.assertEqual(created.status_code, 200, created.text)
        user_id = created.json()["id"]
        self.assertEqual(client.get("/api/users").status_code, 200)

        client.post("/api/auth/logout", headers=admin_headers)
        customer_login = client.post("/api/auth/login", json={
            "username": "customer1", "password": "customer-password-123456"
        })
        self.assertEqual(customer_login.status_code, 200, customer_login.text)
        customer_headers = {"X-CSRF-Token": customer_login.json()["csrf_token"]}
        self.assertEqual(client.get("/api/users").status_code, 403)

        quota_result = client.post("/api/vms", headers=customer_headers, json={
            "name": "customer-vm", "iso_path": "C:\\ISO\\ubuntu.iso",
            "memory_mb": 2048, "disk_gb": 20, "cpu_count": 2
        })
        self.assertEqual(quota_result.status_code, 403, quota_result.text)
        client.post("/api/auth/logout", headers=customer_headers)

        admin_login = client.post("/api/auth/login", json={
            "username": "admin", "password": os.environ["VPS_ADMIN_PASSWORD"]
        })
        admin_headers = {"X-CSRF-Token": admin_login.json()["csrf_token"]}
        updated = client.patch(f"/api/users/{user_id}", headers=admin_headers, json={
            "role": "user", "vm_quota": 2, "active": False
        })
        self.assertEqual(updated.status_code, 200, updated.text)
        suspended = client.post("/api/auth/login", json={
            "username": "customer1", "password": "customer-password-123456"
        })
        self.assertEqual(suspended.status_code, 401)

        deleted = client.delete(f"/api/users/{user_id}", headers=admin_headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(client.get("/api/audit").status_code, 200)
        self.assertEqual(client.delete("/api/users/1", headers=admin_headers).status_code, 400)


if __name__ == "__main__":
    unittest.main()
