import unittest
from app.heartbeat import heartbeat_config, heartbeat_payload


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

    def test_payload_normalizes_resource_usage(self):
        payload = heartbeat_payload({"cpu_percent": 50, "memory_total_mb": 1000, "memory_free_mb": 250, "storage_total_gb": 100, "storage_free_gb": 40}, "libvirt", "0.3.0")
        self.assertEqual(payload["memory_used_mb"], 750)
        self.assertEqual(payload["storage_used_gb"], 60)
        self.assertEqual(payload["provider"], "libvirt")


if __name__ == "__main__":
    unittest.main()
