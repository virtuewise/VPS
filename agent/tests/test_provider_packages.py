import base64
import hashlib
import json
import tempfile
import unittest
import os
from unittest.mock import patch
import zipfile
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from app.providers.package_manager import ProviderRegistry, verify_package
from app.providers.runtime import WorkerProvider

MANIFEST = {
    "manifest_version": 1, "id": "org.example.provider.test", "name": "Test Provider",
    "version": "1.0.0", "provider_api_min": "1.0.0", "provider_api_max": "1.0.0",
    "provider_type": "virtualization", "runtime": "python-worker", "entrypoint": "provider:Provider",
    "capabilities": ["inventory.read", "host.metrics.read", "vm.create", "vm.power"],
    "maintainer": "Example", "license": "MIT"
}


class ProviderPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.package = self.root / "test.vwpkg"
        self.signature = self.root / "test.sig.json"
        self.trust = self.root / "trusted.json"
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.trust.write_text(json.dumps({"test-key": base64.b64encode(public).decode()}))
        with zipfile.ZipFile(self.package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(MANIFEST))
            archive.writestr("provider.py", "class Provider:\n def list_vms(self): return [{\"name\":\"demo\"}]\n def list_switches(self): return []\n def host_monitor(self): return {\"cpu_count\": 2}\n def create_vm(self, **kwargs): return kwargs\n def vm_action(self, action, name, delete_disks=False): return {\"action\": action, \"name\": name}\n")
        digest = hashlib.sha256(self.package.read_bytes()).digest()
        self.signature.write_text(json.dumps({"key_id": "test-key", "signature": base64.b64encode(self.private.sign(digest)).decode()}))

    def tearDown(self): self.temp.cleanup()

    def test_verify_install_enable_and_execute_in_worker(self):
        manifest, digest, key_id = verify_package(self.package, self.signature, self.trust)
        self.assertEqual(manifest.id, MANIFEST["id"])
        registry = ProviderRegistry(self.root / "registry")
        registry.install(self.package, self.signature, self.trust, api_version="1.0.0")
        with self.assertRaisesRegex(ValueError, "not enabled"):
            registry.active_package(manifest.id, trust_store=self.trust, api_version="1.0.0")
        registry.set_enabled(manifest.id, True)
        package, raw_manifest = registry.active_package(manifest.id, trust_store=self.trust, api_version="1.0.0")
        provider = WorkerProvider(package, raw_manifest, sandbox_mode="process")
        self.assertEqual(provider.list_vms(), [{"name": "demo"}])
        self.assertEqual(provider.host_monitor(), {"cpu_count": 2})
        self.assertEqual(provider.vm_action("start", "demo")["action"], "start")
        from app.providers.loader import load_configured_provider
        with patch.dict(os.environ, {"VPS_PROVIDER": "plugin:" + manifest.id, "VPS_PROVIDER_REGISTRY": str(self.root / "registry"), "VPS_PROVIDER_TRUST_STORE": str(self.trust), "VPS_PROVIDER_SANDBOX": "process"}):
            loaded, loaded_manifest, selected = load_configured_provider()
            self.assertEqual(selected, manifest.id)
            self.assertEqual(loaded_manifest.id, manifest.id)
            self.assertEqual(len(loaded.list_vms()), 1)

    def test_capability_and_action_allowlists_are_enforced(self):
        provider = WorkerProvider(self.package, {**MANIFEST, "capabilities": ["inventory.read"]}, sandbox_mode="process")
        with self.assertRaises(PermissionError): provider.create_vm(name="demo")
        with self.assertRaises(PermissionError): provider.create_network(name="tenant-net", mode="isolated", cidr=None)
        with self.assertRaises(ValueError): provider.vm_action("arbitrary", "demo")

    def test_disable_revokes_runtime_calls_and_uninstall_requires_disabled(self):
        registry = ProviderRegistry(self.root / "registry3")
        registry.install(self.package, self.signature, self.trust, api_version="1.0.0")
        registry.set_enabled(MANIFEST["id"], True)
        package, manifest = registry.active_package(MANIFEST["id"], trust_store=self.trust, api_version="1.0.0")
        provider = WorkerProvider(package, manifest, sandbox_mode="process", activation_check=lambda: registry.is_active_enabled(MANIFEST["id"], "1.0.0"))
        self.assertEqual(len(provider.list_vms()), 1)
        with self.assertRaisesRegex(ValueError, "Disable"):
            registry.uninstall(MANIFEST["id"])
        registry.set_enabled(MANIFEST["id"], False)
        with self.assertRaises(PermissionError): provider.list_vms()
        self.assertTrue(registry.uninstall(MANIFEST["id"])["uninstalled"])
        self.assertEqual(registry.list(), [])
        actions = [event["action"] for event in registry.events()]
        self.assertEqual(actions, ["install", "enable", "disable", "uninstall"])

    def test_docker_worker_uses_restrictive_container_flags(self):
        image = "python:3.11-slim@sha256:" + "a" * 64
        with patch.object(WorkerProvider, "_verify_rootless_docker"), patch("app.providers.runtime.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "[]", "")
            provider = WorkerProvider(self.package, MANIFEST, sandbox_mode="docker", sandbox_image=image)
            self.assertEqual(provider.list_vms(), [])
            command = run.call_args.args[0]
        for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m", "--memory-swap=256m", "--cpus=1", "--user=65534:65534"):
            self.assertIn(flag, command)
        self.assertIn(image, command)

    def test_docker_timeout_attempts_to_kill_orphaned_container(self):
        image = "python:3.11-slim@sha256:" + "b" * 64
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            if command[1] == "run":
                cid_path = Path(command[command.index("--cidfile") + 1])
                cid_path.write_text("container-test-id")
                raise subprocess.TimeoutExpired(command, 1)
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(WorkerProvider, "_verify_rootless_docker"), patch("app.providers.runtime.subprocess.run", side_effect=fake_run):
            provider = WorkerProvider(self.package, MANIFEST, sandbox_mode="docker", sandbox_image=image, timeout=1)
            with self.assertRaises(TimeoutError): provider.list_vms()
        self.assertTrue(any(command[:2] == ["docker", "kill"] for command in calls))
        self.assertTrue(any(command[:3] == ["docker", "rm", "-f"] for command in calls))

    def test_docker_worker_rejects_floating_image_tag(self):
        with self.assertRaisesRegex(RuntimeError, "pinned"):
            WorkerProvider(self.package, MANIFEST, sandbox_mode="docker", sandbox_image="python:latest")

    def test_rejects_tampered_signature(self):
        self.package.write_bytes(self.package.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "signature verification"):
            verify_package(self.package, self.signature, self.trust)

    def test_rejects_untrusted_signer(self):
        self.signature.write_text(json.dumps({"key_id": "unknown", "signature": "AA=="}))
        with self.assertRaisesRegex(ValueError, "not trusted"):
            verify_package(self.package, self.signature, self.trust)

    def test_rejects_zip_path_traversal(self):
        bad = self.root / "bad.vwpkg"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("manifest.json", json.dumps(MANIFEST))
            archive.writestr("../evil.py", "pass")
            archive.writestr("provider.py", "class Provider: pass")
        digest = hashlib.sha256(bad.read_bytes()).digest()
        sig = self.root / "bad.sig.json"
        sig.write_text(json.dumps({"key_id": "test-key", "signature": base64.b64encode(self.private.sign(digest)).decode()}))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            verify_package(bad, sig, self.trust)

    def test_registry_upgrade_and_rollback_disable_provider(self):
        registry = ProviderRegistry(self.root / "registry2")
        registry.install(self.package, self.signature, self.trust, api_version="1.0.0")
        registry.set_enabled(MANIFEST["id"], True)
        newer = dict(MANIFEST, version="1.1.0")
        package2 = self.root / "new.vwpkg"
        with zipfile.ZipFile(package2, "w") as archive:
            archive.writestr("manifest.json", json.dumps(newer))
            archive.writestr("provider.py", "class Provider: pass")
        digest = hashlib.sha256(package2.read_bytes()).digest()
        sig2 = self.root / "new.sig.json"
        sig2.write_text(json.dumps({"key_id": "test-key", "signature": base64.b64encode(self.private.sign(digest)).decode()}))
        registry.install(package2, sig2, self.trust, api_version="1.0.0")
        self.assertEqual(registry.rollback(MANIFEST["id"])["active_version"], "1.0.0")
        self.assertFalse(registry.list()[0]["enabled"])


if __name__ == "__main__": unittest.main()
