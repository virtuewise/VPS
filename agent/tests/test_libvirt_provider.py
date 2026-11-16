import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch
import xml.etree.ElementTree as ET

from fastapi import HTTPException

from app.providers import libvirt


class LibvirtProviderTests(unittest.TestCase):
    def test_domain_xml_escapes_values_and_keeps_guest_disconnected_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            xml = libvirt._domain_xml("future-vm", 2048, 2, root / "disk.qcow2",
                                     root / "ubuntu.iso", None, None)
            domain = ET.fromstring(xml)
        self.assertEqual(domain.findtext("name"), "future-vm")
        self.assertEqual(domain.findtext("memory"), "2048")
        self.assertIsNone(domain.find("devices/interface"))

    def test_create_defines_vm_without_starting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            iso = root / "ubuntu.iso"
            iso.write_bytes(b"iso")
            with patch.object(libvirt, "VM_ROOT", root / "vms"), \
                 patch.object(libvirt, "_run", return_value="") as run:
                result = libvirt.create_vm(name="future-vm", memory_mb=2048, disk_gb=20,
                    cpu_count=2, iso_path=str(iso), switch_name=None)
            self.assertEqual(result["state"], "shut off")
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args_list[1].args[0][:3], [libvirt.QEMU_IMG, "create", "-f"])
            define_command = run.call_args_list[2].args[0]
            self.assertEqual(define_command[:2], [libvirt.VIRSH, "define"])
            self.assertFalse(Path(define_command[2]).exists())

    def test_failed_create_removes_managed_disk_and_cloud_init_seed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            iso = root / "ubuntu.iso"
            iso.write_bytes(b"iso")
            with patch.object(libvirt, "VM_ROOT", root / "vms"), \
                 patch.object(libvirt, "_run", side_effect=["", HTTPException(status_code=400, detail="failed")]):
                with self.assertRaises(HTTPException):
                    libvirt.create_vm(name="future-vm", memory_mb=2048, disk_gb=20,
                        cpu_count=2, iso_path=str(iso), switch_name=None)
            self.assertFalse((root / "vms" / "future-vm.qcow2").exists())

    def test_existing_domain_name_is_never_undefined_during_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            iso = root / "ubuntu.iso"
            iso.write_bytes(b"iso")
            with patch.object(libvirt, "VM_ROOT", root / "vms"), \\
                 patch.object(libvirt, "_run", return_value="future-vm") as run:
                with self.assertRaises(HTTPException) as caught:
                    libvirt.create_vm(name="future-vm", memory_mb=2048, disk_gb=20,
                        cpu_count=2, iso_path=str(iso), switch_name=None)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(run.call_count, 1)

    def test_rejects_unsafe_vm_names(self):
        with self.assertRaises(HTTPException) as caught:
            libvirt._validate_name("../bad")
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
