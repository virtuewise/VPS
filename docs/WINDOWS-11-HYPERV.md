# Windows 11 Hyper-V node setup

This guide configures a Windows 11 computer as a local development compute node. It is not a public hosting deployment guide.

## Requirements

- Windows 11 Pro, Enterprise, or Education (Hyper-V role is not available as a supported feature on Windows 11 Home).
- Hardware virtualization enabled in UEFI/BIOS (Intel VT-x or AMD-V).
- SLAT-capable 64-bit CPU and sufficient RAM/disk.
- Python 3.12+.
- An Ubuntu Server ISO downloaded from the official Ubuntu website.

Windows 11 Home users can develop the control plane and use WSL2, but this Hyper-V provider requires a supported Windows edition. Do not use unofficial scripts to force-enable Hyper-V on unsupported editions.

## Enable Hyper-V

Open PowerShell as Administrator and run:

```powershell
Get-ComputerInfo -Property HyperVisorPresent
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
```

Restart Windows when prompted. Confirm the Hyper-V PowerShell module is available:

```powershell
Get-Command Get-VM
Get-VMSwitch
```

Create a virtual switch in Hyper-V Manager if needed. An External switch can expose guest VMs to your LAN; use a Private or Internal switch for isolated testing. Do not expose test VMs directly to the public internet.

## Start the node agent

Open PowerShell in the repository:

```powershell
cd agent
.\run.ps1
```

The agent binds to loopback (127.0.0.1) only. It requires a random bearer token of at least 32 characters. The launcher creates a temporary token if one is not configured; retain it securely for the session.

The agent provides:

- `GET /health` — local health check (no token).
- `GET /v1/vms` — list virtual machines.
- `GET /v1/switches` — list virtual switches.
- `GET /v1/monitor` — host CPU, RAM and VM-volume capacity sample.
- `POST /v1/vms/{name}/console` — launch VMConnect on the node's interactive desktop.
- VM inventory includes virtual NIC, switch, MAC and reported guest IP information.
- `POST /v1/vms` — create a Generation 2 VM with bounded CPU, RAM and disk settings.
- `POST /v1/vms/start` and `POST /v1/vms/stop` — power operations.
- `DELETE /v1/vms/{name}` — remove a VM after exact-name confirmation; managed disks are retained unless separately opted into deletion.

All VM-management endpoints require `Authorization: Bearer <token>`. The agent does not provide arbitrary PowerShell execution.

## Cloud-init and SSH keys

The dashboard can optionally attach a generated Ubuntu autoinstall NoCloud seed ISO containing a Linux username, SHA-512 crypt password hash and SSH public key. Install Windows ADK Deployment Tools (oscdimg.exe) and set VPS_OSCDIMG if it is not on PATH. See [advanced Hyper-V features](HYPERV-ADVANCED-FEATURES.md) for supported inputs and Ubuntu ISO caveats.

## Security and limitations

- Keep the agent bound to localhost until authenticated TLS and node enrollment are implemented.
- Do not port-forward port 8765 or expose it to the internet.
- Run the agent under an account with only the Hyper-V permissions it needs; VM creation and deletion are privileged operations.
- VM deletion requires explicit name confirmation. Disk files are retained by default; opt-in disk deletion is limited to disks under the configured VM root. Back up data first.
- The initial agent is a local milestone and is not yet wired into the dashboard's control-plane node registry.
- Ubuntu ISO boot/install and guest networking should be validated on the target hardware before treating a VM as a usable VPS.
- Hyper-V availability and performance depend on Windows edition, firmware settings, and host hardware.
