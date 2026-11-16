import unittest
from app.scheduler import choose_node


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [
            {"id": 1, "name": "busy", "status": "online", "last_seen": 990, "resources": {"cpu_total": 8, "cpu_used": 6, "memory_total_mb": 16000, "memory_used_mb": 14000, "storage_total_gb": 500, "storage_used_gb": 450, "provider": "libvirt"}},
            {"id": 2, "name": "free", "status": "online", "last_seen": 995, "resources": {"cpu_total": 16, "cpu_used": 1, "memory_total_mb": 64000, "memory_used_mb": 1000, "storage_total_gb": 1000, "storage_used_gb": 100, "provider": "libvirt"}},
        ]

    def test_selects_least_loaded_node(self):
        result = choose_node(self.nodes, cpu_count=2, memory_mb=2048, disk_gb=30, now=1000)
        self.assertEqual(result["node_id"], 2)

    def test_rejects_stale_and_offline_nodes(self):
        result = choose_node(self.nodes, cpu_count=2, memory_mb=2048, disk_gb=30, now=2000)
        self.assertIsNone(result)

    def test_rejects_insufficient_capacity(self):
        result = choose_node(self.nodes, cpu_count=20, memory_mb=2048, disk_gb=30, now=1000)
        self.assertIsNone(result)

    def test_provider_requirement(self):
        result = choose_node(self.nodes, cpu_count=2, memory_mb=2048, disk_gb=30, now=1000, provider="hyperv")
        self.assertIsNone(result)

    def test_reservations_reduce_capacity_and_projected_load(self):
        nodes = [
            {"id": 1, "name": "reserved", "status": "online", "last_seen": 999,
             "resources": {"cpu_total": 8, "cpu_used": 0, "cpu_reserved": 7,
                           "memory_total_mb": 16000, "memory_used_mb": 0, "memory_reserved_mb": 12000,
                           "storage_total_gb": 500, "storage_used_gb": 0, "storage_reserved_gb": 450}},
            {"id": 2, "name": "available", "status": "online", "last_seen": 999,
             "resources": {"cpu_total": 16, "cpu_used": 1, "memory_total_mb": 64000, "memory_used_mb": 1000,
                           "storage_total_gb": 1000, "storage_used_gb": 100}},
        ]
        result = choose_node(nodes, cpu_count=2, memory_mb=2048, disk_gb=20, now=1000)
        self.assertEqual(result["node_id"], 2)
        self.assertIsNone(choose_node(nodes[:1], cpu_count=2, memory_mb=512, disk_gb=8, now=1000))

    def test_switch_and_preferred_node_constraints_are_honored(self):
        nodes = [
            {**self.nodes[0], "switch_names": ["Internal"], "resources": {**self.nodes[0]["resources"],
                "cpu_total": 16, "cpu_used": 1, "memory_total_mb": 64000, "memory_used_mb": 1000,
                "storage_total_gb": 1000, "storage_used_gb": 100}},
            {**self.nodes[1], "switch_names": ["External"]},
        ]
        result = choose_node(nodes, cpu_count=2, memory_mb=2048, disk_gb=30, now=1000, switch_name="Internal")
        self.assertEqual(result["node_id"], 1)
        result = choose_node(nodes, cpu_count=2, memory_mb=2048, disk_gb=30, now=1000, preferred_node_id=1)
        self.assertEqual(result["node_id"], 1)
        self.assertIsNone(choose_node(nodes, cpu_count=2, memory_mb=2048, disk_gb=30, now=1000, switch_name="Missing"))

    def test_missing_resource_telemetry_is_not_schedulable(self):
        result = choose_node([{"id": 3, "name": "unknown", "status": "online", "last_seen": 999, "resources": {}}], cpu_count=1, memory_mb=512, disk_gb=8, now=1000)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
