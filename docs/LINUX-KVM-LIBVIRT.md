# Experimental Linux KVM/libvirt provider

VirtueWise VPS now includes an experimental Linux provider selected through the same node-agent API as Hyper-V. It uses the host's installed `virsh`, `qemu-img`, and (for cloud-init) `cloud-localds` command-line tools. These are open-source components; this does not make the physical server, electricity, bandwidth, public IP address, or operations free.

## Host prerequisites

Use a supported 64-bit Linux distribution with hardware virtualization enabled in firmware and CPU virtualization extensions available. Install and configure:

- KVM and QEMU
- libvirt daemon and client tools (`virsh`)
- `qemu-img`
- `cloud-image-utils` for `cloud-localds` when using cloud-init
- Python 3.10 or newer and the packages in `agent/requirements.txt`

The account running the node agent must have permission to access libvirt and write to the configured VM storage directory. Configure libvirt networking separately; the dashboard lists libvirt virtual networks, not physical bridges or public IP allocations.

## Start the node agent

From the repository root on the Linux node:

```bash
cd agent
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export VPS_PROVIDER=libvirt
export VPS_AGENT_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export VPS_LIBVIRT_VM_ROOT=/var/lib/libvirt/images/virtuewise-vps
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Keep the token available for the control plane. Bind to loopback during development. Do not expose port 8765 publicly. For remote nodes, use a properly authenticated encrypted transport and secure enrollment; the current shared bearer token is a development mechanism, not production node identity.

## Current provider behavior

- Creates a qcow2 disk and defines a KVM domain without starting it
- Attaches the supplied installer ISO and optional cloud-init NoCloud seed ISO
- Supports start, graceful shutdown request, inventory, libvirt network listing, console command guidance, and guarded deletion
- Samples Linux host CPU, memory, and storage capacity
- Reports guest IP addresses only when the QEMU guest agent is installed, running, and able to report them

The Console action currently returns a `virsh console VM_NAME` command for execution on the Linux node. It is not a browser console. The provider does not yet configure public networking, DHCP/IPAM, firewall policy, snapshots, backup, or resource isolation.

## Cloud-init notes

The node needs `cloud-localds`. The generated Ubuntu autoinstall seed contains the supplied SHA-512 crypt password hash and SSH public key, and installs/enables `qemu-guest-agent` for guest IP reporting. The private key must remain on the user's client. Test the exact Ubuntu Server ISO and guest firmware configuration before using it for important workloads.

## Validation status

The provider has automated unit tests for XML generation, safe names, domain definition, and cleanup paths. It has **not** yet been validated against a real Linux KVM/libvirt host. Treat it as experimental until the roadmap's host acceptance tests pass.
