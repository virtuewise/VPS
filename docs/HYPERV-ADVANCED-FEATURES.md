# Hyper-V advanced VM features

These are local Windows-node development features, not a remote console or production hosting stack.

## Console access

The VM inventory's Console action asks the node agent to launch vmconnect.exe on the Windows Hyper-V host. It is not an HTML5/VNC/noVNC browser console and does not stream a screen through the control plane. It is enabled for running VMs only. The agent must run in the interactive Windows user session for the console window to be visible.

## SSH keys and Ubuntu unattended installation

The VM creation form accepts an optional cloud-init set: a lowercase Linux username, a SHA-512 crypt password hash (starts with $6$; plaintext passwords are rejected), and one SSH public key (ssh-ed25519 or ssh-rsa).

When all three fields are supplied, the agent creates a NoCloud seed ISO labelled cidata, containing Ubuntu autoinstall configuration, and attaches it as a second DVD alongside the Ubuntu Server installer ISO. The guest is configured to install the OpenSSH server, install the supplied key, and disable SSH password authentication.

Install the Windows ADK Deployment Tools and make oscdimg.exe available on PATH, or set VPS_OSCDIMG to its full path before starting the agent. Seed files are written beneath the configured VM root. Do not share or commit generated seed media: it contains account configuration and the public key.

Use a current Ubuntu Server live installer ISO that supports Subiquity autoinstall and NoCloud. Depending on the Ubuntu ISO release, unattended mode may require adding the autoinstall kernel boot parameter. Verify the installer reaches the automated flow in a disposable VM before relying on it. This agent has not been tested against a physical Hyper-V host or every Ubuntu ISO release.

Generate a SHA-512 crypt hash using a trusted local Linux mkpasswd --method=sha-512 utility or equivalent. Do not paste a plaintext password into this form. Keep the private SSH key on your client device; only its public key belongs in the control panel.

## Guest network information

VM inventory includes Hyper-V virtual NIC switch names, MAC addresses and IP addresses reported by Hyper-V guest reporting. IPs may be empty until the guest boots, obtains DHCP, and reports addresses. A virtual switch must be attached and configured for the intended connectivity. This is not a public IP allocator, firewall manager, or DHCP service.

## Deletion safeguards

The dashboard requires the exact VM name, then separately asks whether managed disk files should also be permanently removed. The API requires a matching confirmation name. By default, VM configuration is removed while disk files are retained. If disk deletion is explicitly selected, only virtual disks whose paths are inside the configured VM root are eligible. Other disks are retained.

The agent requests a guest shutdown and refuses deletion if the VM does not reach the Off state. Shut down the guest cleanly and retry rather than force-powering it off. Back up data before deletion.

## Node resource monitoring

The monitor reports a point-in-time sample of host CPU utilization, physical memory, and free/total capacity of the volume containing the configured VM root. Values are not historical graphs and do not represent per-tenant billing. VM inventory also reports per-VM CPU usage, assigned memory, virtual NICs and guest IPs where Hyper-V exposes them.

## Security boundary

The control plane now has local username/password accounts, administrator and customer roles, signed expiring sessions, CSRF protection, login throttling, VM ownership checks, VM-count quotas, and administrative audit events. These are useful development controls, not a completed production security review. Nodes now use single-use enrollment, hashed per-node credentials, authenticated outbound heartbeats and a typed outbound command queue. Mutual TLS, multi-process queue coordination and independent security review remain outstanding. Multi-node scheduling and node-routed provisioning are implemented but still need real-host validation.

Keep both the control plane and agent bound to loopback while developing. Do not expose the agent or an unaudited control plane to the LAN or internet. Console launch is a privileged host-side action. Back up the SQLite database and VM disks before upgrades or destructive operations.


## Isolated virtual switches

VirtueWise can create and remove private Hyper-V virtual switches through the authenticated node command channel. The provider refuses to remove a switch while any VM adapter is attached and protects the built-in Default Switch. Private switches connect attached guests to each other but do not provide host access, DHCP, NAT or public connectivity. Configure guest IPs and any required routing/DHCP service separately; do not treat a private switch as a complete hosting network.
