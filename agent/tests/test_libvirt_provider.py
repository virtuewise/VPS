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
        self.assertIsNotNone(domain.find("devices/channel/target[@name=\"org.qemu.guest_agent.0\"]"))

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
            with patch.object(libvirt, "VM_ROOT", root / "vms"), patch.object(libvirt, "_run", return_value="future-vm") as run:
                with self.assertRaises(HTTPException) as caught:
                    libvirt.create_vm(name="future-vm", memory_mb=2048, disk_gb=20,
                        cpu_count=2, iso_path=str(iso), switch_name=None)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(run.call_count, 1)

    def test_power_actions_are_idempotent_for_running_and_stopped_domains(self):
        with patch.object(libvirt, "_run", return_value="running") as run:
            self.assertEqual(libvirt.vm_action("start", "future-vm")["state"], "running")
            run.assert_called_once_with([libvirt.VIRSH, "domstate", "future-vm"])
        with patch.object(libvirt, "_run", return_value="shut off") as run:
            self.assertEqual(libvirt.vm_action("stop", "future-vm")["state"], "shut off")
            run.assert_called_once_with([libvirt.VIRSH, "domstate", "future-vm"])

    def test_network_xml_defines_isolated_dhcp_and_nat_modes(self):
        isolated = ET.fromstring(libvirt._network_xml("tenant-a", "isolated", "192.168.70.0/24"))
        self.assertIsNone(isolated.find("forward"))
        self.assertEqual(isolated.find("ip/dhcp/range").get("start"), "192.168.70.2")
        self.assertEqual(isolated.find("ip/dhcp/range").get("end"), "192.168.70.254")
        nat = ET.fromstring(libvirt._network_xml("tenant-b", "nat", "10.70.0.0/24"))
        self.assertEqual(nat.find("forward").get("mode"), "nat")
        self.assertEqual(nat.find("ip").get("address"), "10.70.0.1")

    def test_rejects_invalid_network_ranges(self):
        for cidr in ("192.168.1.1/24", "192.168.1.0/30", "2001:db8::/64", "bad"):
            with self.subTest(cidr=cidr), self.assertRaises(HTTPException):
                libvirt._network_xml("tenant-a", "isolated", cidr)

    def test_creates_and_autostarts_libvirt_network(self):
        with patch.object(libvirt, "_run", side_effect=["", "", "", ""]) as run:
            result = libvirt.create_network(name="tenant-a", mode="nat", cidr="192.168.70.0/24")
        self.assertTrue(result["active"])
        self.assertEqual(result["gateway"], "192.168.70.1")
        self.assertEqual(run.call_args_list[0].args[0], [libvirt.VIRSH, "net-list", "--all", "--name"])
        self.assertEqual(run.call_args_list[2].args[0], [libvirt.VIRSH, "net-autostart", "tenant-a"])
        self.assertEqual(run.call_args_list[3].args[0], [libvirt.VIRSH, "net-start", "tenant-a"])

    def test_rejects_overlapping_libvirt_network_cidr(self):
        existing = "<network><name>default</name><ip address='192.168.70.1' netmask='255.255.255.0'/></network>"
        with patch.object(libvirt, "_run", side_effect=["default", existing]):
            with self.assertRaises(HTTPException) as caught:
                libvirt.create_network(name="tenant-a", mode="nat", cidr="192.168.70.128/25")
        self.assertEqual(caught.exception.status_code, 409)

    def test_refuses_network_deletion_when_attached_to_vm(self):
        with patch.object(libvirt, "_run", side_effect=["tenant-a", "demo-vm", "Interface Type Source Model MAC\n--------------------------------------\nvnet0 network tenant-a virtio 52:54:00:00:00:01"]):
            with self.assertRaises(HTTPException) as caught:
                libvirt.delete_network("tenant-a")
        self.assertEqual(caught.exception.status_code, 409)

    def test_protects_builtin_default_network(self):
        with self.assertRaises(HTTPException) as caught:
            libvirt.delete_network("default")
        self.assertEqual(caught.exception.status_code, 409)

    def test_deletes_unused_active_network(self):
        with patch.object(libvirt, "_run", side_effect=["tenant-a", "", "Name: tenant-a\nActive: yes", "", ""]) as run:
            result = libvirt.delete_network("tenant-a")
        self.assertTrue(result["deleted"])
        self.assertEqual(run.call_args_list[-2].args[0], [libvirt.VIRSH, "net-destroy", "tenant-a"])
        self.assertEqual(run.call_args_list[-1].args[0], [libvirt.VIRSH, "net-undefine", "tenant-a"])

    def test_rejects_unsafe_vm_names(self):
        with self.assertRaises(HTTPException) as caught:
            libvirt._validate_name("../bad")
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
