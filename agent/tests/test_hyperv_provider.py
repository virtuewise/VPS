import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.providers import hyperv


class HyperVProviderTests(unittest.TestCase):
    def test_rejects_invalid_vm_names(self):
        with self.assertRaises(HTTPException) as caught:
            hyperv._validate_name("../outside")
        self.assertEqual(caught.exception.status_code, 422)

    def test_rejects_plaintext_cloud_init_password_before_creating_files(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(hyperv, "VM_ROOT", Path(temp)):
                with self.assertRaises(HTTPException) as caught:
                    hyperv._make_cloud_init_seed("test-vm", "ubuntu", "plaintext-password", "ssh-ed25519 AAAA test")
                self.assertEqual(caught.exception.status_code, 422)
                self.assertFalse((Path(temp) / "test-vm").exists())

    def test_failed_provisioning_removes_generated_seed_media(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            iso = root / "ubuntu.iso"
            iso.write_bytes(b"test")
            seed_dir = root / "test-vm" / "cloud-init"
            seed_dir.mkdir(parents=True)
            (seed_dir / "user-data").write_text("#cloud-config")
            seed_iso = root / "test-vm" / "test-vm-seed.iso"
            seed_iso.write_bytes(b"seed")
            with patch.object(hyperv, "VM_ROOT", root), \
                 patch.object(hyperv, "_make_cloud_init_seed", return_value=seed_iso), \
                 patch.object(hyperv, "_run", side_effect=HTTPException(status_code=400, detail="simulated failure")):
                with self.assertRaises(HTTPException):
                    hyperv.create_vm(
                        name="test-vm", memory_mb=2048, disk_gb=20, cpu_count=2,
                        iso_path=str(iso), switch_name=None,
                        cloud_init={"username": "ubuntu", "password_hash": "$6$invalid", "ssh_public_key": "ssh-ed25519 AAAA"}
                    )
            self.assertFalse(seed_iso.exists())
            self.assertFalse(seed_dir.exists())


if __name__ == "__main__":
    unittest.main()
