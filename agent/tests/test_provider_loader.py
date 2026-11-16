import os
import unittest
from unittest.mock import patch

from app.providers.loader import load_configured_provider


class ProviderLoaderTests(unittest.TestCase):
    def test_builtin_provider_loads_manifest_and_contract(self):
        provider, manifest, name = load_configured_provider("hyperv")
        self.assertEqual(name, "hyperv")
        self.assertEqual(manifest.id, "com.virtuewise.provider.hyperv")
        self.assertTrue(callable(provider.list_vms))

    def test_rejects_unknown_provider(self):
        with self.assertRaisesRegex(RuntimeError, "VPS_PROVIDER"):
            load_configured_provider("unknown")

    def test_plugin_requires_registry_and_trust_store(self):
        with patch.dict(os.environ, {"VPS_PROVIDER_REGISTRY": "", "VPS_PROVIDER_TRUST_STORE": ""}):
            with self.assertRaisesRegex(RuntimeError, "VPS_PROVIDER_REGISTRY"):
                load_configured_provider("plugin:org.example.provider.demo")


if __name__ == "__main__": unittest.main()
