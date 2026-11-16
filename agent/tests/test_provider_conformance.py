import unittest
from app.providers.interface import validate_provider


class ContractFixture:
    name = "contract-fixture"
    def list_vms(self): return []
    def list_switches(self): return []
    def host_monitor(self): return {"cpu_count": 1}
    def create_vm(self, *, name, memory_mb, disk_gb, cpu_count, iso_path, switch_name=None, cloud_init=None):
        return {"name": name}
    def vm_action(self, action, name, *, delete_disks=False): return {"action": action, "name": name}


class ProviderConformanceTests(unittest.TestCase):
    def test_provider_contract_methods_and_normalized_results(self):
        provider = ContractFixture()
        validate_provider(provider)
        self.assertIsInstance(provider.list_vms(), list)
        self.assertIsInstance(provider.list_switches(), list)
        self.assertIsInstance(provider.host_monitor(), dict)
        self.assertEqual(provider.create_vm(name="demo", memory_mb=512, disk_gb=8, cpu_count=1, iso_path="/tmp/a.iso")["name"], "demo")
        self.assertEqual(provider.vm_action("start", "demo")["action"], "start")

    def test_missing_method_fails_closed(self):
        class Incomplete:
            def list_vms(self): return []
        with self.assertRaisesRegex(TypeError, "list_switches"):
            validate_provider(Incomplete())


if __name__ == "__main__":
    unittest.main()
