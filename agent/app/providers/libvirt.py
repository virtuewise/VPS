from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi import HTTPException

VM_ROOT = Path(os.getenv("VPS_LIBVIRT_VM_ROOT", "/var/lib/libvirt/images/virtuewise-vps")).resolve()
VIRSH = os.getenv("VPS_VIRSH", "virsh")
QEMU_IMG = os.getenv("VPS_QEMU_IMG", "qemu-img")
CLOUD_LOCALDS = os.getenv("VPS_CLOUD_LOCALDS", "cloud-localds")
TIMEOUT_SECONDS = 120


def _run(command: list[str], *, timeout: int = TIMEOUT_SECONDS) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="libvirt operation timed out.") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Required Linux virtualization command is unavailable.") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:] or "libvirt operation failed."
        raise HTTPException(status_code=400, detail=detail)
    return result.stdout.strip()


def _validate_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,39}", name):
        raise HTTPException(status_code=422, detail="Invalid VM name.")


def _domain_xml(name: str, memory_mb: int, cpu_count: int, disk: Path, iso: Path,
                seed_iso: Path | None, switch_name: str | None) -> str:
    domain = ET.Element("domain", {"type": "kvm"})
    ET.SubElement(domain, "name").text = name
    ET.SubElement(domain, "memory", {"unit": "MiB"}).text = str(memory_mb)
    ET.SubElement(domain, "currentMemory", {"unit": "MiB"}).text = str(memory_mb)
    ET.SubElement(domain, "vcpu", {"placement": "static"}).text = str(cpu_count)
    os_node = ET.SubElement(domain, "os")
    ET.SubElement(os_node, "type", {"arch": "x86_64", "machine": "q35"}).text = "hvm"
    ET.SubElement(os_node, "boot", {"dev": "cdrom"})
    ET.SubElement(os_node, "boot", {"dev": "hd"})
    ET.SubElement(domain, "features").append(ET.Element("acpi"))
    devices = ET.SubElement(domain, "devices")
    disk_node = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
    ET.SubElement(disk_node, "driver", {"name": "qemu", "type": "qcow2"})
    ET.SubElement(disk_node, "source", {"file": str(disk)})
    ET.SubElement(disk_node, "target", {"dev": "vda", "bus": "virtio"})
    for media in [iso, *([seed_iso] if seed_iso else [])]:
        cd = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
        ET.SubElement(cd, "driver", {"name": "qemu", "type": "raw"})
        ET.SubElement(cd, "source", {"file": str(media)})
        ET.SubElement(cd, "target", {"dev": "sda" if media == iso else "sdb", "bus": "sata"})
        ET.SubElement(cd, "readonly")
    if switch_name:
        iface = ET.SubElement(devices, "interface", {"type": "network"})
        ET.SubElement(iface, "source", {"network": switch_name})
        ET.SubElement(iface, "model", {"type": "virtio"})
    ET.SubElement(devices, "serial", {"type": "pty"}).append(ET.Element("target", {"port": "0"}))
    ET.SubElement(devices, "console", {"type": "pty"}).append(ET.Element("target", {"type": "serial", "port": "0"}))
    channel = ET.SubElement(devices, "channel", {"type": "unix"})
    ET.SubElement(channel, "source", {"mode": "bind"})
    ET.SubElement(channel, "target", {"type": "virtio", "name": "org.qemu.guest_agent.0"})
    ET.SubElement(devices, "graphics", {"type": "vnc", "autoport": "yes", "listen": "127.0.0.1"})
    return ET.tostring(domain, encoding="unicode")


def list_switches() -> list[dict[str, Any]]:
    names = _run([VIRSH, "net-list", "--all", "--name"]).splitlines()
    return [{"name": name.strip(), "switch_type": "libvirt network"} for name in names if name.strip()]


def list_vms() -> list[dict[str, Any]]:
    names = [n.strip() for n in _run([VIRSH, "list", "--all", "--name"]).splitlines() if n.strip()]
    items = []
    for name in names:
        state = "Unknown"
        for line in _run([VIRSH, "domstate", name]).splitlines():
            if line.strip():
                state = line.strip().capitalize()
                break
        info = _run([VIRSH, "dominfo", name])
        details = {}
        for line in info.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                details[key.strip().lower()] = value.strip()
        memory_kib = re.sub(r"[^0-9]", "", details.get("max memory", "0")) or "0"
        networks = []
        try:
            rows = _run([VIRSH, "domiflist", name]).splitlines()[2:]
            addresses = {}
            try:
                for row in _run([VIRSH, "domifaddr", name, "--source", "agent", "--full"]).splitlines()[2:]:
                    cols = row.split()
                    if len(cols) >= 4:
                        addresses.setdefault(cols[1].lower(), []).append(cols[3].split("/")[0])
            except HTTPException:
                pass
            for row in rows:
                cols = row.split()
                if len(cols) >= 5:
                    networks.append({"switch_name": cols[2], "mac_address": cols[4],
                                     "ip_addresses": addresses.get(cols[4].lower(), [])})
        except HTTPException:
            pass
        items.append({"name": name, "state": state, "cpu_usage": None,
                      "memory_assigned_mb": int(memory_kib) // 1024,
                      "uptime": None, "id": name, "network": networks})
    return items


def host_monitor() -> dict[str, Any]:
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            first = [int(x) for x in handle.readline().split()[1:]]
        time.sleep(0.1)
        with open("/proc/stat", encoding="utf-8") as handle:
            second = [int(x) for x in handle.readline().split()[1:]]
        idle1, idle2 = first[3] + first[4], second[3] + second[4]
        total1, total2 = sum(first), sum(second)
        cpu = 0.0 if total2 == total1 else 100 * (1 - (idle2 - idle1) / (total2 - total1))
        mem = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                mem[key] = int(value.strip().split()[0])
        VM_ROOT.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(VM_ROOT)
    except (OSError, ValueError, IndexError) as exc:
        raise HTTPException(status_code=503, detail="Could not sample Linux host resources.") from exc
    total_mb = mem.get("MemTotal", 0) // 1024
    free_mb = (mem.get("MemAvailable", mem.get("MemFree", 0))) // 1024
    return {"cpu_percent": round(max(0.0, min(100.0, cpu)), 1),
            "memory_total_mb": total_mb, "memory_free_mb": free_mb,
            "storage_total_gb": round(usage.total / (1024 ** 3), 1),
            "storage_free_gb": round(usage.free / (1024 ** 3), 1),
            "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def create_vm(*, name: str, memory_mb: int, disk_gb: int, cpu_count: int, iso_path: str,
              switch_name: str | None, cloud_init: dict[str, str] | None = None) -> Any:
    _validate_name(name)
    iso = Path(iso_path).expanduser().resolve()
    if not iso.is_file() or iso.suffix.lower() != ".iso":
        raise HTTPException(status_code=400, detail="iso_path must point to an existing ISO file on the Linux node.")
    VM_ROOT.mkdir(parents=True, exist_ok=True)
    disk = (VM_ROOT / f"{name}.qcow2").resolve()
    if VM_ROOT not in disk.parents or disk.exists():
        raise HTTPException(status_code=409, detail="Managed VM disk already exists or has an invalid path.")
    existing_names = {item.strip() for item in _run([VIRSH, "list", "--all", "--name"]).splitlines()}
    if name in existing_names:
        raise HTTPException(status_code=409, detail="A libvirt VM with this name already exists.")
    if switch_name and switch_name not in {x["name"] for x in list_switches()}:
        raise HTTPException(status_code=400, detail="Requested libvirt network does not exist.")
    seed_iso = None
    seed_dir = VM_ROOT / name / "cloud-init"
    seed_iso = VM_ROOT / f"{name}-seed.iso"
    if seed_iso.exists() or seed_dir.exists():
        raise HTTPException(status_code=409, detail="Cloud-init seed files already exist for this VM name.")
    domain_defined = False
    try:
        if cloud_init:
            username = cloud_init["username"]
            password_hash = cloud_init["password_hash"]
            key = cloud_init["ssh_public_key"].strip()
            if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
                raise HTTPException(status_code=422, detail="Invalid Linux username.")
            if not re.fullmatch(r"\$6\$(?:rounds=[0-9]+\$)?[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}", password_hash):
                raise HTTPException(status_code=422, detail="Provide a valid SHA-512 crypt password hash.")
            if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]{0,100})?|ssh-rsa [A-Za-z0-9+/=]+(?: [^\r\n]{0,100})?", key):
                raise HTTPException(status_code=422, detail="Provide one valid SSH public key.")
            seed_dir.mkdir(parents=True, exist_ok=True)
            (seed_dir / "user-data").write_text(
                f"#cloud-config\nautoinstall:\n  version: 1\n  identity:\n    hostname: {name}\n    username: {username}\n    password: '{password_hash}'\n  ssh:\n    install-server: true\n    allow-pw: false\n    authorized-keys:\n      - {json.dumps(key)}\n  packages:\n    - qemu-guest-agent\n  late-commands:\n    - curtin in-target --target=/target -- systemctl enable qemu-guest-agent\n",
                encoding="utf-8")
            (seed_dir / "meta-data").write_text(f"instance-id: {name}\nlocal-hostname: {name}\n", encoding="utf-8")
            _run([CLOUD_LOCALDS, str(seed_iso), str(seed_dir / "user-data"), str(seed_dir / "meta-data")])
        _run([QEMU_IMG, "create", "-f", "qcow2", str(disk), f"{disk_gb}G"])
        xml = _domain_xml(name, memory_mb, cpu_count, disk, iso, seed_iso, switch_name)
        xml_path = VM_ROOT / f"{name}.xml"
        xml_path.write_text(xml, encoding="utf-8")
        try:
            _run([VIRSH, "define", str(xml_path)])
            domain_defined = True
        finally:
            xml_path.unlink(missing_ok=True)
        return {"name": name, "state": "shut off", "disk": str(disk)}
    except Exception:
        if domain_defined:
            try:
                _run([VIRSH, "undefine", name, "--nvram"])
            except Exception:
                pass
        disk.unlink(missing_ok=True)
        if seed_iso:
            seed_iso.unlink(missing_ok=True)
        if seed_dir.exists():
            shutil.rmtree(seed_dir, ignore_errors=True)
        raise


def vm_action(action: str, name: str, *, delete_disks: bool = False) -> Any:
    _validate_name(name)
    if action == "start":
        current = _run([VIRSH, "domstate", name]).strip().lower()
        if current == "running":
            return {"name": name, "state": "running"}
        _run([VIRSH, "start", name])
        return {"name": name, "state": "running"}
    if action == "stop":
        current = _run([VIRSH, "domstate", name]).strip().lower()
        if current in {"shut off", "shutoff"}:
            return {"name": name, "state": "shut off"}
        _run([VIRSH, "shutdown", name])
        return {"name": name, "state": "shutdown-requested"}
        return {"name": name, "state": "shutdown-requested"}
    if action == "console":
        _run([VIRSH, "domstate", name])
        return {"opened": False, "name": name, "command": f"virsh console {name}",
                "message": "Run the command in a terminal on the Linux node. Browser console access is not enabled yet."}
    if action == "delete":
        state = _run([VIRSH, "domstate", name]).strip().lower()
        if state not in {"shut off", "shutoff", "crashed"}:
            raise HTTPException(status_code=409, detail="VM must be shut off before deletion.")
        _run([VIRSH, "undefine", name, "--nvram"])
        disk = (VM_ROOT / f"{name}.qcow2").resolve()
        if delete_disks and VM_ROOT in disk.parents:
            disk.unlink(missing_ok=True)
        seed_iso = VM_ROOT / f"{name}-seed.iso"
        seed_iso.unlink(missing_ok=True)
        seed_dir = VM_ROOT / name / "cloud-init"
        if seed_dir.exists():
            shutil.rmtree(seed_dir, ignore_errors=True)
        return {"deleted": True, "name": name, "disks_deleted": delete_disks}
    raise ValueError("Unsupported VM action")
