import unittest

from app.providers.interface import validate_provider


class ValidProvider:
    def list_vms(self): return []
    def list_switches(self): return []
    def host_monitor(self): return {}
    def create_vm(self, **kwargs): return {}
    def vm_action(self, action, name, **kwargs): return {}


class ProviderInterfaceTests(unittest.TestCase):
    def test_accepts_structurally_compatible_provider(self):
        validate_provider(ValidProvider())

    def test_rejects_provider_missing_required_operations(self):
        with self.assertRaisesRegex(TypeError, "list_vms"):
            validate_provider(object())


if __name__ == "__main__":
    unittest.main()
