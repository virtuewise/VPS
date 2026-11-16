import json
import tempfile
import unittest
from pathlib import Path

from app.providers.capabilities import validate_capabilities
from app.providers.manifest import ProviderManifest, load_provider_manifest

ROOT = Path(__file__).resolve().parents[1]


class ProviderManifestTests(unittest.TestCase):
    def test_builtin_manifests_are_valid_and_compatible(self):
        for name in ("hyperv", "libvirt"):
            manifest = load_provider_manifest(ROOT / "provider-manifests" / f"{name}.json", api_version="1.0.0")
            self.assertEqual(manifest.id, f"com.virtuewise.provider.{name}")
            self.assertEqual(manifest.runtime, "builtin")
            self.assertIn("vm.create", manifest.capabilities)
            self.assertIn("network.manage", manifest.capabilities)

    def test_rejects_incompatible_api(self):
        with self.assertRaisesRegex(ValueError, "supports API"):
            load_provider_manifest(ROOT / "provider-manifests/hyperv.json", api_version="2.0.0")

    def test_rejects_unknown_manifest_fields(self):
        raw = json.loads((ROOT / "provider-manifests/hyperv.json").read_text())
        raw["python_entrypoint"] = "attacker.module:run"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider.json"
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                load_provider_manifest(path, api_version="1.0.0")

    def test_rejects_unknown_or_duplicate_capabilities(self):
        with self.assertRaisesRegex(ValueError, "unknown capabilities"):
            validate_capabilities(["host.root"])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            validate_capabilities(["vm.create", "vm.create"])

    def test_rejects_invalid_semver_and_reversed_range(self):
        raw = json.loads((ROOT / "provider-manifests/hyperv.json").read_text())
        raw["version"] = "v1"
        with self.assertRaises(ValueError):
            ProviderManifest.model_validate(raw)
        raw["version"] = "1.0.0"
        raw["provider_api_min"], raw["provider_api_max"] = "2.0.0", "1.0.0"
        with self.assertRaises(ValueError):
            ProviderManifest.model_validate(raw)

    def test_rejects_symlink_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.json"
            target.write_text((ROOT / "provider-manifests/hyperv.json").read_text())
            link = Path(directory) / "link.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("Symlinks unavailable")
            with self.assertRaisesRegex(ValueError, "non-symlink"):
                load_provider_manifest(link, api_version="1.0.0")


if __name__ == "__main__":
    unittest.main()
