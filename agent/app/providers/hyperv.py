from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import HTTPException

POWERSHELL = os.getenv("VPS_POWERSHELL", "powershell.exe")
VM_ROOT = Path(os.getenv("VPS_HYPERV_VM_ROOT", r"C:\VirtueWise\VMs")).resolve()
OSCDIMG = os.getenv("VPS_OSCDIMG", "oscdimg.exe")
TIMEOUT_SECONDS = 120

POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$payload = $env:VPS_HYPERV_PAYLOAD | ConvertFrom-Json
$result = $null
switch ($env:VPS_HYPERV_ACTION) {
  'list' {
    $result = @(Get-VM | ForEach-Object {
      $vm = $_
      $nics = @(Get-VMNetworkAdapter -VM $vm | ForEach-Object {
        @{ switch_name = $_.SwitchName; mac_address = $_.MacAddress; ip_addresses = @($_.IPAddresses | Where-Object { $_ }) }
      })
      [pscustomobject]@{
        Name = $vm.Name; State = [string]$vm.State; CPUUsage = $vm.CPUUsage
        MemoryAssigned = $vm.MemoryAssigned; Uptime = [string]$vm.Uptime; Id = [string]$vm.Id
        Network = $nics
      }
    })
  }
  'create' {
    $name = [string]$payload.name
    if (Get-VM -Name $name -ErrorAction SilentlyContinue) { throw 'A VM with this name already exists.' }
    $switch = [string]$payload.switch_name
    if ($switch -and -not (Get-VMSwitch -Name $switch -ErrorAction SilentlyContinue)) { throw 'The requested virtual switch does not exist.' }
    try {
      $params = @{
        Name = $name; Generation = 2
        MemoryStartupBytes = ([int64]$payload.memory_mb * 1MB)
        NewVHDPath = [string]$payload.vhd_path
        NewVHDSizeBytes = ([int64]$payload.disk_gb * 1GB)
      }
      if ($switch) { $params.SwitchName = $switch }
      New-VM @params | Out-Null
      Set-VMProcessor -VMName $name -Count ([int]$payload.cpu_count)
      Set-VMFirmware -VMName $name -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority
      Add-VMDvdDrive -VMName $name -Path ([string]$payload.iso_path) | Out-Null
      if ($payload.seed_iso_path) { Add-VMDvdDrive -VMName $name -Path ([string]$payload.seed_iso_path) | Out-Null }
      $result = Get-VM -Name $name | Select-Object Name, State, Id
    } catch {
      $partial = Get-VM -Name $name -ErrorAction SilentlyContinue
      if ($partial) { Remove-VM -VM $partial -Force -ErrorAction SilentlyContinue }
      $partialDisk = [string]$payload.vhd_path
      if ($partialDisk -and (Test-Path -LiteralPath $partialDisk)) { Remove-Item -LiteralPath $partialDisk -Force -ErrorAction SilentlyContinue }
      throw
    }
  }
  'start' {
    Start-VM -Name ([string]$payload.name) | Out-Null
    $result = Get-VM -Name ([string]$payload.name) | Select-Object Name, State, Id
  }
  'stop' {
    $vm = Get-VM -Name ([string]$payload.name) -ErrorAction Stop
    if ($vm.State -ne 'Off') { Stop-VM -VM $vm -Shutdown -ErrorAction Stop }
    $result = Get-VM -Name ([string]$payload.name) | Select-Object Name, State, Id
  }
  'delete' {
    $name = [string]$payload.name
    $vm = Get-VM -Name $name -ErrorAction Stop
    if ($vm.State -ne 'Off') {
      Stop-VM -VM $vm -Shutdown -ErrorAction Stop
      $vm = Get-VM -Name $name
      if ($vm.State -ne 'Off') { throw 'Guest did not shut down. Shut it down inside the guest, then retry deletion.' }
    }
    $managed = @()
    foreach ($drive in (Get-VMHardDiskDrive -VM $vm)) {
      if ($drive.Path) {
        $full = [IO.Path]::GetFullPath($drive.Path)
        $root = [IO.Path]::GetFullPath($env:VPS_HYPERV_VM_ROOT).TrimEnd('\') + '\'
        if ($full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { $managed += $full }
      }
    }
    Remove-VM -VM $vm -Force
    if ([bool]$payload.delete_disks) {
      foreach ($disk in $managed) { if (Test-Path -LiteralPath $disk) { Remove-Item -LiteralPath $disk -Force } }
    }
    $seedIso = Join-Path $env:VPS_HYPERV_VM_ROOT "$name-seed.iso"
    $seedDir = Join-Path (Join-Path $env:VPS_HYPERV_VM_ROOT $name) 'cloud-init'
    if (Test-Path -LiteralPath $seedIso) { Remove-Item -LiteralPath $seedIso -Force }
    if (Test-Path -LiteralPath $seedDir) { Remove-Item -LiteralPath $seedDir -Recurse -Force }
    $result = @{ deleted = $true; name = $name; disks_deleted = [bool]$payload.delete_disks; retained_disk_paths = @($managed | Where-Object { Test-Path -LiteralPath $_ }) }
  }
  'switches' { $result = @(Get-VMSwitch | Select-Object Name, SwitchType) }
  'network-create' {
    $name = [string]$payload.name
    if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{1,62}$') { throw 'Invalid virtual switch name.' }
    if (Get-VMSwitch -Name $name -ErrorAction SilentlyContinue) { throw 'A virtual switch with this name already exists.' }
    if ([string]$payload.mode -ne 'isolated' -or $payload.cidr) { throw 'Hyper-V supports isolated private switches only; CIDR/DHCP configuration is not managed by this provider.' }
    $created = New-VMSwitch -Name $name -SwitchType Private
    $result = @{ name = $created.Name; switch_type = [string]$created.SwitchType; isolated = $true }
  }
  'network-delete' {
    $name = [string]$payload.name
    if (-not (Get-VMSwitch -Name $name -ErrorAction SilentlyContinue)) { throw 'Virtual switch not found.' }
    $attached = @(Get-VMNetworkAdapter -All | Where-Object { $_.SwitchName -eq $name })
    if ($attached.Count -gt 0) { throw 'Virtual switch is attached to one or more VMs; detach their adapters before deletion.' }
    Remove-VMSwitch -Name $name -Force
    $result = @{ deleted = $true; name = $name }
  }
  'console' {
    $vm = Get-VM -Name ([string]$payload.name) -ErrorAction Stop
    $exe = Join-Path $env:SystemRoot 'System32\vmconnect.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw 'Hyper-V Virtual Machine Connection is unavailable.' }
    Start-Process -FilePath $exe -ArgumentList @($env:COMPUTERNAME, $vm.Name)
    $result = @{ opened = $true; name = $vm.Name; host = $env:COMPUTERNAME }
  }
  'monitor' {
    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction SilentlyContinue).CounterSamples[0].CookedValue
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:VPS_HYPERV_DRIVE)'"
    $result = @{
      cpu_percent = [math]::Round([double]$cpu, 1)
      memory_total_mb = [math]::Round([double]$os.TotalVisibleMemorySize / 1024)
      memory_free_mb = [math]::Round([double]$os.FreePhysicalMemory / 1024)
      storage_total_gb = [math]::Round([double]$disk.Size / 1GB, 1)
      storage_free_gb = [math]::Round([double]$disk.FreeSpace / 1GB, 1)
      sampled_at = (Get-Date).ToUniversalTime().ToString('o')
    }
  }
  default { throw 'Unsupported operation.' }
}
$result | ConvertTo-Json -Depth 7 -Compress
"""


def _run(action: str, payload: dict[str, Any] | None = None) -> Any:
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="The Hyper-V provider requires a Windows host.")
    env = os.environ.copy()
    env["VPS_HYPERV_ACTION"] = action
    env["VPS_HYPERV_PAYLOAD"] = json.dumps(payload or {})
    env["VPS_HYPERV_VM_ROOT"] = str(VM_ROOT)
    env["VPS_HYPERV_DRIVE"] = VM_ROOT.drive
    try:
        completed = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SCRIPT],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Hyper-V operation timed out.") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="PowerShell is unavailable on this host.") from exc
    if completed.returncode != 0:
        raise HTTPException(status_code=400, detail="Hyper-V operation failed. Check the host's Hyper-V event logs.")
    output = completed.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Unexpected response from Hyper-V.") from exc


def list_vms() -> list[dict[str, Any]]:
    rows = _run("list") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [{
        "name": row.get("Name"), "state": row.get("State"), "cpu_usage": row.get("CPUUsage"),
        "memory_assigned_mb": round(int(row.get("MemoryAssigned") or 0) / (1024 * 1024)),
        "uptime": row.get("Uptime"), "id": row.get("Id"), "network": row.get("Network") or [],
    } for row in rows]


def list_switches() -> list[dict[str, Any]]:
    rows = _run("switches") or []
    if isinstance(rows, dict):
        rows = [rows]
    return [{"name": row.get("Name"), "switch_type": row.get("SwitchType")} for row in rows]


def _validate_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,39}", name):
        raise HTTPException(status_code=422, detail="Invalid VM name.")


def _make_cloud_init_seed(name: str, username: str, password_hash: str, ssh_public_key: str) -> Path:
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise HTTPException(status_code=422, detail="Linux username must be lowercase and start with a letter or underscore.")
    if re.fullmatch(r"\$6\$(?:rounds=[0-9]+\$)?[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}", password_hash) is None:
        raise HTTPException(status_code=422, detail="Provide a valid SHA-512 crypt password hash; plain-text passwords are not accepted.")
    key = ssh_public_key.strip()
    if not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]{0,100})?|ssh-rsa [A-Za-z0-9+/=]+(?: [^\r\n]{0,100})?", key):
        raise HTTPException(status_code=422, detail="Provide one valid ssh-ed25519 or ssh-rsa public key.")
    if shutil.which(OSCDIMG) is None:
        raise HTTPException(status_code=503, detail="Cloud-init ISO creation needs oscdimg.exe (install the Windows ADK Deployment Tools and set VPS_OSCDIMG).")
    seed_dir = VM_ROOT / name / "cloud-init"
    seed_dir.mkdir(parents=True, exist_ok=True)
    user_data = f"""#cloud-config
autoinstall:
  version: 1
  identity:
    hostname: {name}
    username: {username}
    password: '{password_hash}'
  ssh:
    install-server: true
    allow-pw: false
    authorized-keys:
      - {json.dumps(key)}
  packages:
    - qemu-guest-agent
  late-commands:
    - curtin in-target --target=/target -- systemctl enable qemu-guest-agent
"""
    (seed_dir / "user-data").write_text(user_data, encoding="utf-8", newline="\n")
    (seed_dir / "meta-data").write_text(f"instance-id: {name}\nlocal-hostname: {name}\n", encoding="utf-8")
    (seed_dir / "vendor-data").write_text("#cloud-config\n", encoding="utf-8")
    iso_path = seed_dir.parent / f"{name}-seed.iso"
    completed = subprocess.run(
        [OSCDIMG, "-j2", "-lcidata", str(seed_dir), str(iso_path)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if completed.returncode != 0 or not iso_path.is_file():
        raise HTTPException(status_code=500, detail="Could not build cloud-init seed ISO with oscdimg.")
    return iso_path


def create_vm(*, name: str, memory_mb: int, disk_gb: int, cpu_count: int, iso_path: str,
              switch_name: str | None, cloud_init: dict[str, str] | None = None) -> Any:
    _validate_name(name)
    iso = Path(iso_path).expanduser().resolve()
    if not iso.is_file() or iso.suffix.lower() != ".iso":
        raise HTTPException(status_code=400, detail="iso_path must point to an existing ISO file on the Windows host.")
    VM_ROOT.mkdir(parents=True, exist_ok=True)
    vhd = (VM_ROOT / f"{name}.vhdx").resolve()
    if VM_ROOT not in vhd.parents:
        raise HTTPException(status_code=400, detail="Invalid virtual disk path.")
    if vhd.exists():
        raise HTTPException(status_code=409, detail="A managed virtual disk with this name already exists.")
    seed_iso = None
    if cloud_init:
        seed_iso = _make_cloud_init_seed(name, cloud_init["username"], cloud_init["password_hash"], cloud_init["ssh_public_key"])
    try:
        return _run("create", {
            "name": name, "memory_mb": memory_mb, "disk_gb": disk_gb, "cpu_count": cpu_count,
            "iso_path": str(iso), "vhd_path": str(vhd), "switch_name": switch_name or "",
            "seed_iso_path": str(seed_iso) if seed_iso else "",
        })
    except Exception:
        if seed_iso:
            seed_iso.unlink(missing_ok=True)
            seed_dir = seed_iso.parent / "cloud-init"
            if seed_dir.exists():
                shutil.rmtree(seed_dir, ignore_errors=True)
            try:
                seed_iso.parent.rmdir()
            except OSError:
                pass
        raise


def vm_action(action: str, name: str, *, delete_disks: bool = False) -> Any:
    _validate_name(name)
    if action not in {"start", "stop", "delete", "console"}:
        raise ValueError("Unsupported VM action")
    if action == "delete":
        return _run(action, {"name": name, "delete_disks": delete_disks})
    return _run(action, {"name": name})


def host_monitor() -> dict[str, Any]:
    return _run("monitor") or {}


def create_network(*, name: str, mode: str, cidr: str | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,62}", name):
        raise HTTPException(status_code=422, detail="Invalid virtual network name.")
    if mode != "isolated" or cidr:
        raise HTTPException(status_code=422, detail="Hyper-V provider supports isolated private switches only; CIDR/DHCP is not managed here.")
    return _run("network-create", {"name": name, "mode": mode, "cidr": None})


def delete_network(name: str) -> dict[str, Any]:
    if isinstance(name, str) and name.casefold() == "default switch":
        raise HTTPException(status_code=409, detail="The built-in Hyper-V Default Switch is protected.")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,62}", name):
        raise HTTPException(status_code=422, detail="Invalid virtual network name.")
    return _run("network-delete", {"name": name})
