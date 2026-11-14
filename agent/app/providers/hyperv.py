from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import HTTPException

POWERSHELL = os.getenv("VPS_POWERSHELL", "powershell.exe")
VM_ROOT = Path(os.getenv("VPS_HYPERV_VM_ROOT", r"C:\VirtueWise\VMs")).resolve()
TIMEOUT_SECONDS = 90

# This script is static. User-provided values are passed as JSON through the
# process environment and never interpolated into PowerShell source.
POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$payload = $env:VPS_HYPERV_PAYLOAD | ConvertFrom-Json
$result = $null

switch ($env:VPS_HYPERV_ACTION) {
  'list' {
    $result = @(Get-VM | Select-Object Name, State, CPUUsage, MemoryAssigned, Uptime, Id)
  }
  'create' {
    $name = [string]$payload.name
    $memory = [int64]$payload.memory_mb * 1MB
    $disk = [int64]$payload.disk_gb * 1GB
    $iso = [string]$payload.iso_path
    $vhd = [string]$payload.vhd_path
    $cpu = [int]$payload.cpu_count
    $switch = [string]$payload.switch_name

    if ($switch) {
      if (-not (Get-VMSwitch -Name $switch -ErrorAction SilentlyContinue)) {
        throw 'The requested virtual switch does not exist.'
      }
    }

    $params = @{
      Name = $name
      Generation = 2
      MemoryStartupBytes = $memory
      NewVHDPath = $vhd
      NewVHDSizeBytes = $disk
    }
    if ($switch) { $params.SwitchName = $switch }

    $vm = New-VM @params
    Set-VMProcessor -VMName $name -Count $cpu
    Set-VMFirmware -VMName $name -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority
    Add-VMDvdDrive -VMName $name -Path $iso
    $result = Get-VM -Name $name | Select-Object Name, State, Id
  }
  'start' {
    Start-VM -Name ([string]$payload.name) | Out-Null
    $result = Get-VM -Name ([string]$payload.name) | Select-Object Name, State, Id
  }
  'stop' {
    Stop-VM -Name ([string]$payload.name) -TurnOff -Force | Out-Null
    $result = Get-VM -Name ([string]$payload.name) | Select-Object Name, State, Id
  }
  'delete' {
    $vm = Get-VM -Name ([string]$payload.name) -ErrorAction Stop
    if ($vm.State -ne 'Off') { Stop-VM -VM $vm -TurnOff -Force }
    Remove-VM -VM $vm -Force
    $vhd = Join-Path $env:VPS_HYPERV_VM_ROOT ([string]$payload.name + '.vhdx')
    if (Test-Path -LiteralPath $vhd) { Remove-Item -LiteralPath $vhd -Force }
    $result = @{ deleted = $true; name = [string]$payload.name }
  }
  'switches' {
    $result = @(Get-VMSwitch | Select-Object Name, SwitchType)
  }
  default { throw 'Unsupported operation.' }
}

$result | ConvertTo-Json -Depth 5 -Compress
"""


def _run(action: str, payload: dict[str, Any] | None = None) -> Any:
    if os.name != "nt":
        raise HTTPException(
            status_code=501,
            detail="The Hyper-V provider requires a Windows host. Use a Linux KVM provider on Linux nodes.",
        )

    env = os.environ.copy()
    env["VPS_HYPERV_ACTION"] = action
    env["VPS_HYPERV_PAYLOAD"] = json.dumps(payload or {})
    env["VPS_HYPERV_VM_ROOT"] = str(VM_ROOT)

    try:
        completed = subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_SCRIPT],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Hyper-V operation timed out.") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="PowerShell is unavailable on this host.") from exc

    if completed.returncode != 0:
        # Do not return raw PowerShell output, which may disclose host details.
        raise HTTPException(status_code=400, detail="Hyper-V operation failed. Check the host's Hyper-V event logs.")
    output = completed.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Unexpected response from Hyper-V.") from exc


def list_vms() -> list[dict[str, Any]]:
    return _run("list") or []


def list_switches() -> list[dict[str, Any]]:
    return _run("switches") or []


def create_vm(*, name: str, memory_mb: int, disk_gb: int, cpu_count: int, iso_path: str, switch_name: str | None) -> Any:
    iso = Path(iso_path).expanduser().resolve()
    if not iso.is_file() or iso.suffix.lower() != ".iso":
        raise HTTPException(status_code=400, detail="iso_path must point to an existing ISO file on the Windows host.")

    VM_ROOT.mkdir(parents=True, exist_ok=True)
    vhd = (VM_ROOT / f"{name}.vhdx").resolve()
    if VM_ROOT not in vhd.parents:
        raise HTTPException(status_code=400, detail="Invalid virtual disk path.")

    return _run("create", {
        "name": name,
        "memory_mb": memory_mb,
        "disk_gb": disk_gb,
        "cpu_count": cpu_count,
        "iso_path": str(iso),
        "vhd_path": str(vhd),
        "switch_name": switch_name or "",
    })


def vm_action(action: str, name: str) -> Any:
    if action not in {"start", "stop", "delete"}:
        raise ValueError("Unsupported VM action")
    return _run(action, {"name": name})
