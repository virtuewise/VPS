const byId = id => document.getElementById(id);
let csrfToken = "";
let currentUser = null;
const notice = message => { byId("notice").textContent = message; };

async function api(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    csrfToken = "";
    byId("login-gate").hidden = false;
  }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
}
function setCreateEnabled(enabled) {
  byId("create-vm-button").disabled = !enabled;
  byId("create-vm-status").textContent = enabled ? "Agent connected. Review settings before provisioning." : "Connect the local agent to enable provisioning.";
}
async function waitForProvisioning(jobId) {
  for (let attempt = 0; attempt < 180; attempt++) {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    byId("create-vm-status").textContent = `Provisioning ${job.vm_name}: ${job.status} (attempt ${job.attempts})`;
    if (job.status === "succeeded") return job;
    if (job.status === "failed") throw new Error(job.error || "Provisioning failed. Check job details before retrying.");
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error(`Provisioning is still running. Job ID: ${jobId}`);
}
async function loadSwitches() {
  const select = byId("switch-select"), selected = select.value, result = await api("/api/switches");
  select.replaceChildren(new Option("No switch (disconnected)", ""));
  for (const item of result.items || []) {
    const value = item.node_id ? JSON.stringify({node_id:item.node_id,name:item.name}) : item.name;
    const label = item.node_name ? `${item.name} ? ${item.node_name}` : `${item.name} ? ${item.switch_type || "virtual switch"}`;
    select.add(new Option(label, value));
  }
  if ([...select.options].some(option => option.value === selected)) select.value = selected;
}
function renderVMs(items) {
  const content = byId("vm-content");
  byId("vm-count").textContent = String(items.length);
  byId("vm-summary").textContent = items.length ? `VMs across ${new Set(items.map(vm=>vm.node_id).filter(Boolean)).size || 1} connected node(s)` : "No VMs found on connected nodes";
  if (!items.length) { content.className="empty"; content.innerHTML='<div class="empty-icon">▣</div><b>No virtual machines found</b><p class="muted">Use the form above to create your first VM.</p>'; return; }
  content.className="vm-list";
  content.innerHTML=items.map(vm=>{
    const name=escapeHtml(vm.name), state=escapeHtml(vm.state||"Unknown"), running=String(vm.state||"").toLowerCase()==="running";
    const net=(vm.network||[]).flatMap(n=>(n.ip_addresses||[]).map(ip=>`${escapeHtml(ip)} (${escapeHtml(n.switch_name||"unattached")})`));
    const netText=net.length?net.join(", "):"No guest IP reported yet";
    return `<div class="vm-row vm-row-expanded"><div class="vm-ident"><b>${name}</b><span class="muted">${escapeHtml(vm.node_name||"Configured agent")} ? CPU ${escapeHtml(vm.cpu_usage??"—")}% · ${Number(vm.memory_assigned_mb||0)} MB assigned</span><span class="muted">Network: ${netText}</span></div><span class="state ${running?"running":""}">${state}</span><div class="vm-actions"><button class="secondary" data-action="console" data-name="${name}" ${running?"":"disabled"}>Console</button><button class="secondary" data-action="${running?"stop":"start"}" data-name="${name}">${running?"Stop":"Start"}</button><button class="danger" data-action="delete" data-name="${name}">Delete</button></div></div>`;
  }).join("");
}
async function loadMonitor() {
  const target=byId("monitor-content");
  try {
    const m=await api("/api/monitor");
    if(m.restricted){target.innerHTML=`<div class="empty">${escapeHtml(m.message||"Host metrics are administrator-only.")}</div>`;return;}
    const memUsed=Math.max(0,m.memory_total_mb-m.memory_free_mb), storageUsed=Math.max(0,m.storage_total_gb-m.storage_free_gb);
    const metrics=[
      ["Host CPU",`${m.cpu_percent}%`,m.cpu_percent],
      ["Memory",`${(memUsed/1024).toFixed(1)} / ${(m.memory_total_mb/1024).toFixed(1)} GB`,m.memory_total_mb?memUsed/m.memory_total_mb*100:0],
      ["VM storage volume",`${m.storage_free_gb} GB free / ${m.storage_total_gb} GB`,m.storage_total_gb?storageUsed/m.storage_total_gb*100:0]
    ];
    target.innerHTML=metrics.map(([label,value,pct])=>`<article class="resource-card"><span class="muted">${label}</span><b>${value}</b><div class="meter"><span style="width:${Math.min(100,Math.max(0,Number(pct)||0))}%"></span></div></article>`).join("");
  } catch(error) { target.innerHTML=`<div class="empty">${escapeHtml(error.message)}</div>`; }
}
function updateMcpScopeOptions() {
  const selected = byId('mcp-token-user')?.selectedOptions?.[0];
  const customer = selected?.textContent?.endsWith('(user)');
  for (const checkbox of document.querySelectorAll('#mcp-token-form input[name="scopes"]')) {
    if (checkbox.value === 'nodes.read' || checkbox.value === 'resources.read') {
      checkbox.disabled = Boolean(customer);
      if (customer) checkbox.checked = false;
    }
  }
}
byId('mcp-token-user').addEventListener('change', updateMcpScopeOptions);

function updateNetworkForm() {
  const node = byId('network-node').selectedOptions[0], provider = node?.dataset.provider || '';
  const capabilities = JSON.parse(node?.dataset.capabilities || '[]');
  const mode = byId('network-mode'), cidr = byId('network-cidr'), submit = byId('network-form').querySelector('button[type="submit"]');
  const hyperv = provider === 'hyperv', libvirt = provider === 'libvirt';
  const pluginNetwork = !hyperv && !libvirt && capabilities.includes('network.manage');
  const supported = hyperv || libvirt || pluginNetwork;
  mode.querySelector('option[value="nat"]').disabled = hyperv || !supported;
  if ((hyperv || !supported) && mode.value === 'nat') mode.value = 'isolated';
  cidr.disabled = hyperv || !supported;
  cidr.required = libvirt || pluginNetwork;
  if (hyperv) cidr.value = '';
  submit.disabled = !supported;
}
byId('network-node').addEventListener('change', updateNetworkForm);
byId('network-mode').addEventListener('change', updateNetworkForm);

async function loadAdminPanel() {
  if (!currentUser || currentUser.role !== 'admin') { byId('administration').hidden = true; byId('admin-nav').hidden = true; return; }
  byId('admin-nav').hidden = false;
  byId('administration').hidden = false;
  const result = await api('/api/users');
  const select = byId('transfer-user-select'); select.replaceChildren();
  for (const u of result.items) if (u.active && u.role === 'user') select.add(new Option(u.username, String(u.id)));
  const tokenOwner = byId('mcp-token-user'); tokenOwner.replaceChildren();
  for (const u of result.items) if (u.active) tokenOwner.add(new Option(`${u.username} (${u.role})`, String(u.id)));
  updateMcpScopeOptions();
  byId('users-content').innerHTML = result.items.map(u => '<div class="user-row" data-user-id="'+u.id+'"><b>'+escapeHtml(u.username)+'</b><select data-field="role"><option value="user" '+(u.role==='user'?'selected':'')+'>Customer</option><option value="admin" '+(u.role==='admin'?'selected':'')+'>Administrator</option></select><input data-field="quota" type="number" min="0" max="1000" value="'+u.vm_quota+'"><select data-field="active"><option value="true" '+(u.active?'selected':'')+'>Active</option><option value="false" '+(!u.active?'selected':'')+'>Suspended</option></select><button class="secondary" data-user-action="save">Save</button><button class="danger" data-user-action="delete" '+(u.id===currentUser.id?'disabled':'')+'>Delete</button></div>').join('') || '<p>No accounts yet.</p>';
  const [log,tokens,nodes,recoveryJobs] = await Promise.all([api('/api/audit?limit=30'), api('/api/mcp/tokens'), api('/api/nodes'), api('/api/admin/provisioning-jobs')]);
  const networks = await api('/api/networks');
  byId('audit-content').innerHTML = log.items.map(e => '<div class="audit-row"><b>'+escapeHtml(e.action)+'</b> · '+escapeHtml(e.actor_username)+' · '+escapeHtml(e.target)+' <span class="muted">'+escapeHtml(e.created_at)+'</span></div>').join('') || 'No events recorded.';
  byId('mcp-tokens-content').innerHTML = tokens.items.map(t => '<div class="mcp-token-row"><div><b>'+escapeHtml(t.label)+'</b><span class="muted">'+escapeHtml(t.username)+' ? '+escapeHtml(t.scopes.join(', '))+' ? expires '+escapeHtml(new Date(t.expires_at*1000).toLocaleDateString())+'</span><span class="muted">'+(t.revoked_at?'Revoked':t.expires_at*1000<Date.now()?'Expired':'Active')+'</span></div><button type="button" class="danger" data-mcp-token-revoke="'+t.id+'" '+(t.revoked_at?'disabled':'')+'>Revoke</button></div>').join('') || 'No scoped MCP tokens issued.';
  byId('node-management-content').innerHTML = nodes.items.map(n => '<div class="mcp-token-row"><div><b>'+escapeHtml(n.name)+'</b><span class="muted">'+escapeHtml(n.hostname)+' ? '+escapeHtml(n.status)+' ? '+Number(n.vm_count||0)+' VMs ? last heartbeat '+escapeHtml(n.last_seen?new Date(n.last_seen*1000).toLocaleString():'never')+'</span></div><button type="button" class="danger" data-node-revoke="'+n.id+'" '+(n.status==='revoked'?'disabled':'')+'>Revoke</button></div>').join('') || 'No compute nodes enrolled.';
  byId('provisioning-recovery-content').innerHTML=(recoveryJobs.items||[]).map(job=>`<div class="mcp-token-row"><div><b>${escapeHtml(job.vm_name)}</b><span class="muted">${escapeHtml(job.username)} | ${escapeHtml(job.node_name||`Node ${job.node_id}`)} | failed after ${Number(job.attempts)} attempt(s)</span><span class="muted">${escapeHtml(job.error||'No provider error recorded')} | heartbeat ${job.last_seen?escapeHtml(new Date(job.last_seen*1000).toLocaleString()):'never'} | VM inventory ${job.inventory_seen?escapeHtml(new Date(job.inventory_seen*1000).toLocaleString()):'never'} | updated ${escapeHtml(job.updated_at)}</span></div><div class="network-row-actions"><button type="button" class="secondary" data-job-reconcile="${escapeHtml(job.id)}" data-reconcile-action="confirm_present" ${job.can_reconcile?'':'disabled'}>Confirm VM exists</button><button type="button" class="danger" data-job-reconcile="${escapeHtml(job.id)}" data-reconcile-action="confirm_absent" ${job.can_reconcile?'':'disabled'}>Confirm VM absent</button></div></div>`).join('') || 'No unresolved node provisioning jobs.';
  const nodeSelect=byId('network-node'); nodeSelect.replaceChildren();
  for(const n of nodes.items) if(n.status==='online') { const option=new Option(`${n.name} (${n.resources?.provider||'unknown'})`,String(n.id)); option.dataset.provider=n.resources?.provider||''; option.dataset.capabilities=JSON.stringify(n.resources?.capabilities||[]); nodeSelect.add(option); }
  updateNetworkForm();
  const customerOptions=result.items.filter(u=>u.active&&u.role==='user').map(u=>`<option value="${u.id}">${escapeHtml(u.username)}</option>`).join('');
  byId('networks-content').innerHTML=(networks.items||[]).map(n=>{
    const assignAction=n.assigned_user_id?`<span class="muted">Assigned to ${escapeHtml(n.assigned_username||'customer')}</span><button type="button" class="secondary" data-network-unassign data-network-node="${n.node_id}" data-network-name="${escapeHtml(n.name)}" >Unassign</button>`:`<select data-network-assign-user ${n.status!=='active'||!customerOptions?'disabled':''}><option value="">Assign to customer?</option>${customerOptions}</select><button type="button" class="secondary" data-network-assign data-network-node="${n.node_id}" data-network-name="${escapeHtml(n.name)}" ${n.status!=='active'||!customerOptions?'disabled':''}>Assign</button>`;
    const deleteDisabled=n.status!=='active'&&n.status!=='missing'||Boolean(n.assigned_user_id);
    return `<div class="mcp-token-row"><div><b>${escapeHtml(n.name)}</b><span class="muted">${escapeHtml(n.node_name)} | ${escapeHtml(n.mode)} | ${escapeHtml(n.cidr||'no CIDR')} | ${escapeHtml(n.status)}</span></div><div class="network-row-actions">${assignAction}<button type="button" class="danger" data-network-delete="${escapeHtml(n.name)}" data-network-node="${n.node_id}" ${deleteDisabled?'disabled':''}>${n.status==='missing'?'Forget record':'Delete'}</button></div></div>`;
  }).join('') || 'No VirtueWise-managed virtual networks.';
}

async function loadDashboard() {
  try {
    const [health,nodes,agent]=await Promise.all([api("/api/health"),api("/api/nodes"),api("/api/agent/status")]);
    byId("api-status").textContent=health.status==="ok"?"Control plane API online":"API status unknown";
    byId("node-count").textContent=String(nodes.items.length);
    byId("agent-status").textContent=agent.online?"Online":"Offline";
    byId("agent-status").className=agent.online?"online":"offline";
    setCreateEnabled(agent.online);
    if(nodes.items.length){byId("nodes-content").className="node-list";byId("nodes-content").innerHTML=nodes.items.map(n=>`<div class="node-row"><b>${escapeHtml(n.name)}</b><span>${escapeHtml(n.hostname||"Compute node")}</span><span>${escapeHtml(n.status)} ? ${Number(n.vm_count||0)} VMs</span></div>`).join("");}
    if(agent.online){const [vms]=await Promise.all([api("/api/vms"),loadSwitches()]);renderVMs(vms.items||[]);await loadMonitor();}
    else{byId("vm-count").textContent="?";byId("vm-summary").textContent="No nodes currently connected";byId("vm-content").className="empty";byId("vm-content").innerHTML=`<b>${escapeHtml(agent.message||"Node agent is offline")}</b><p class="muted">Start an enrolled node agent with VPS_CONTROL_PLANE_URL, VPS_NODE_ID and VPS_NODE_TOKEN.</p>`;byId("monitor-content").textContent="Node agent offline";}
  } catch(error){byId("api-status").textContent="API unavailable";setCreateEnabled(false);notice(error.message);}
}
byId("create-vm-form").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
  const v=new FormData(form), username=String(v.get("linux_username")||"").trim(), hash=String(v.get("password_hash")||"").trim(), key=String(v.get("ssh_public_key")||"").trim();
  const cloudFields=[username,hash,key], supplied=cloudFields.filter(Boolean).length;
  if(supplied!==0&&supplied!==3){notice("For cloud-init, fill in Linux username, SHA-512 password hash and SSH public key.");return;}
  const selectedSwitch=String(v.get("switch_name")||"");
  let switchName=null, nodeId=null;
  if(selectedSwitch){try{const choice=JSON.parse(selectedSwitch);if(choice&&typeof choice==="object"){switchName=choice.name;nodeId=Number(choice.node_id);}}catch{switchName=selectedSwitch;}}
  const payload={name:String(v.get("name")).trim(),iso_path:String(v.get("iso_path")).trim(),memory_mb:Number(v.get("memory_mb")),disk_gb:Number(v.get("disk_gb")),cpu_count:Number(v.get("cpu_count")),switch_name:switchName};
  if(Number.isSafeInteger(nodeId)&&nodeId>0)payload.node_id=nodeId;
  if(supplied===3)payload.cloud_init={username,password_hash:hash,ssh_public_key:key};
  const summary=`${payload.name}: ${payload.cpu_count} vCPU, ${payload.memory_mb} MB RAM, ${payload.disk_gb} GB disk`;
  if(!confirm(`Create this VM?\n\n${summary}\nCloud-init: ${supplied===3?"enabled":"not enabled"}\n\nIt will be created powered off.`))return;
  const button=byId("create-vm-button");button.disabled=true;byId("create-vm-status").textContent="Creating VM…";notice("");
  try{const queued=await api("/api/vms",{method:"POST",body:JSON.stringify(payload)});byId("create-vm-status").textContent=`Queued (${queued.provisioning_job_id}).`;await waitForProvisioning(queued.provisioning_job_id);notice(`VM ${payload.name} created powered off.`);form.reset();form.elements.memory_mb.value="2048";form.elements.disk_gb.value="20";form.elements.cpu_count.value="2";await loadDashboard();}
  catch(error){byId("create-vm-status").textContent="Creation failed.";notice(error.message);setCreateEnabled(true);if(error.message.includes("Job ID:"))notice(`${error.message} You can safely refresh; the job continues on the server.`);}
});
byId('user-create-form').addEventListener('submit', async event => { event.preventDefault(); const f=event.currentTarget; if(!f.reportValidity()) return; const v=new FormData(f); try { await api('/api/users',{method:'POST',body:JSON.stringify({username:String(v.get('username')).trim(),password:String(v.get('password')),role:String(v.get('role')),vm_quota:Number(v.get('vm_quota'))})}); f.reset(); f.elements.vm_quota.value='3'; notice('Account created.'); await loadAdminPanel(); } catch(e) { notice(e.message); } });
byId('users-content').addEventListener('click', async event => { const b=event.target.closest('button[data-user-action]'); if(!b) return; const row=b.closest('[data-user-id]'), id=Number(row.dataset.userId); try { if(b.dataset.userAction==='save') { await api('/api/users/'+id,{method:'PATCH',body:JSON.stringify({role:row.querySelector('[data-field=role]').value,vm_quota:Number(row.querySelector('[data-field=quota]').value),active:row.querySelector('[data-field=active]').value==='true'})}); notice('Account updated.'); await loadAdminPanel(); } else if(confirm('Delete this account? Transfer its VMs first.')) { await api('/api/users/'+id,{method:'DELETE'}); notice('Account deleted.'); await loadAdminPanel(); } } catch(e) { notice(e.message); } });
byId('network-form').addEventListener('submit', async event => {
  event.preventDefault(); const form=event.currentTarget; if(!form.reportValidity()) return;
  const values=new FormData(form), payload={node_id:Number(values.get('node_id')),name:String(values.get('name')).trim(),mode:String(values.get('mode')),cidr:String(values.get('cidr')||'').trim()||null};
  if(!confirm(`Create ${payload.mode} network ${payload.name} on node ${byId('network-node').selectedOptions[0]?.textContent}?`))return;
  try { await api('/api/networks',{method:'POST',body:JSON.stringify(payload)}); notice('Virtual network created.'); form.elements.name.value=''; await loadAdminPanel(); await loadDashboard(); }
  catch(error){notice(error.message);}
});
byId('provisioning-recovery-content').addEventListener('click', async event => {
  const button=event.target.closest('button[data-job-reconcile]'); if(!button||button.disabled)return;
  const jobId=button.dataset.jobReconcile, action=button.dataset.reconcileAction;
  const phrase=action==='confirm_present'?'confirm that the VM exists':'confirm that the VM is absent and release its ownership/quota reservation';
  if(!confirm(`The control plane will verify the latest fresh inventory from the assigned node. Do you ${phrase}? This does not retry provisioning.`))return;
  try { const result=await api(`/api/admin/provisioning-jobs/${encodeURIComponent(jobId)}/reconcile`,{method:'POST',body:JSON.stringify({action})}); notice(result.message); await loadAdminPanel(); await loadDashboard(); }
  catch(error){notice(error.message);}
});

byId('networks-content').addEventListener('click', async event => {
  const assignButton=event.target.closest('button[data-network-assign]');
  if(assignButton && !assignButton.disabled) {
    const row=assignButton.closest('.mcp-token-row'), select=row.querySelector('select[data-network-assign-user]');
    const userId=Number(select?.value), nodeId=assignButton.dataset.networkNode, name=assignButton.dataset.networkName;
    if(!Number.isSafeInteger(userId)||userId<1){notice('Choose a customer account first.');return;}
    try { await api(`/api/networks/${nodeId}/${encodeURIComponent(name)}/assign`,{method:'POST',body:JSON.stringify({user_id:userId})}); notice('Network assigned to customer.'); await loadAdminPanel(); }
    catch(error){notice(error.message);} return;
  }
  const unassignButton=event.target.closest('button[data-network-unassign]');
  if(unassignButton && !unassignButton.disabled) {
    const nodeId=unassignButton.dataset.networkNode, name=unassignButton.dataset.networkName;
    if(!confirm(`Unassign ${name} from this customer? Existing attached VMs must be removed first.`))return;
    try { await api(`/api/networks/${nodeId}/${encodeURIComponent(name)}/assign`,{method:'DELETE'}); notice('Network unassigned.'); await loadAdminPanel(); }
    catch(error){notice(error.message);} return;
  }
  const deleteButton=event.target.closest('button[data-network-delete]'); if(!deleteButton || deleteButton.disabled)return;
  const name=deleteButton.dataset.networkDelete, nodeId=deleteButton.dataset.networkNode;
  if(!confirm(`Delete or reconcile VirtueWise-managed network ${name}? Provider deletion is refused while any VM is attached.`))return;
  try { await api(`/api/networks/${nodeId}/${encodeURIComponent(name)}`,{method:'DELETE'}); notice('Virtual network deleted or reconciled.'); await loadAdminPanel(); await loadDashboard(); }
  catch(error){notice(error.message);}
});

byId('node-enroll-form').addEventListener('submit', async event => {
  event.preventDefault(); const form=event.currentTarget; if(!form.reportValidity()) return;
  const values=new FormData(form);
  try {
    const result=await api('/api/nodes/enrollment-tokens',{method:'POST',body:JSON.stringify({name:String(values.get('name')).trim(),hostname:String(values.get('hostname')).trim()})});
    byId('node-enroll-value').textContent=result.token; byId('node-enroll-once').hidden=false;
    notice('One-time node enrollment token created. Deliver it securely; it expires in 15 minutes.'); form.reset(); await loadAdminPanel();
  } catch(error){notice(error.message);}
});
byId('node-enroll-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(byId('node-enroll-value').textContent); notice('Enrollment token copied.'); }
  catch { notice('Clipboard unavailable. Select and copy the token manually.'); }
});
byId('node-management-content').addEventListener('click', async event => {
  const button=event.target.closest('button[data-node-revoke]'); if(!button)return;
  if(!confirm('Revoke this node credential immediately? It will no longer be able to report telemetry or receive commands.'))return;
  try { await api('/api/nodes/'+button.dataset.nodeRevoke+'/revoke',{method:'POST'}); notice('Node credential revoked.'); await loadAdminPanel(); await loadDashboard(); }
  catch(error){notice(error.message);}
});

byId('mcp-token-form').addEventListener('submit', async event => {
  event.preventDefault(); const form=event.currentTarget; if(!form.reportValidity()) return;
  const values=new FormData(form), scopes=[...form.querySelectorAll('input[name="scopes"]:checked')].map(input=>input.value);
  if(!scopes.length){notice('Select at least one MCP permission.');return;}
  try {
    const result=await api('/api/mcp/tokens',{method:'POST',body:JSON.stringify({user_id:Number(values.get('user_id')),label:String(values.get('label')).trim(),scopes,expires_in_days:Number(values.get('expires_in_days'))})});
    byId('mcp-token-value').textContent=result.token; byId('mcp-token-once').hidden=false;
    notice('MCP token issued. Copy and store it securely; it cannot be retrieved later.'); await loadAdminPanel();
  } catch(error){notice(error.message);}
});
byId('mcp-token-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(byId('mcp-token-value').textContent); notice('MCP token copied.'); }
  catch { notice('Clipboard access unavailable. Select and copy the token manually.'); }
});
byId('mcp-tokens-content').addEventListener('click', async event => {
  const button=event.target.closest('button[data-mcp-token-revoke]'); if(!button)return;
  if(!confirm('Revoke this MCP token immediately? Existing clients using it will lose access.'))return;
  try { await api('/api/mcp/tokens/'+button.dataset.mcpTokenRevoke+'/revoke',{method:'POST'}); notice('MCP token revoked.'); await loadAdminPanel(); }
  catch(error){notice(error.message);}
});
byId('transfer-vm-form').addEventListener('submit', async event => { event.preventDefault(); const f=event.currentTarget; if(!f.reportValidity()) return; const v=new FormData(f), name=String(v.get('vm_name')).trim(), id=String(v.get('user_id')); if(!confirm('Transfer VM '+name+' to this customer?')) return; try { await api('/api/users/'+id+'/transfer-vm?vm_name='+encodeURIComponent(name),{method:'POST'}); notice('VM ownership transferred.'); f.reset(); } catch(e) { notice(e.message); } });
byId("refresh-button").addEventListener("click",loadDashboard);
byId("monitor-refresh").addEventListener("click",loadMonitor);
byId("vm-content").addEventListener("click",async event=>{
  const button=event.target.closest("button[data-action]");if(!button)return;
  const {action,name}=button.dataset;
  if(action==="stop"&&!confirm(`Gracefully shut down VM "${name}"?`))return;
  if(action==="delete"){
    if(prompt(`Type the exact VM name "${name}" to remove the VM (its virtual disks will be retained):`)!==name)return;
    const removeDisks=confirm("Also permanently delete this VM's managed virtual disk files? This cannot be undone. Choose Cancel to retain disks.");
    if(removeDisks&&prompt("Type DELETE DISKS to confirm permanent disk removal:")!=="DELETE DISKS")return;
    button.disabled=true;
    try{await api(`/api/vms/${encodeURIComponent(name)}?confirm_name=${encodeURIComponent(name)}&delete_disks=${removeDisks}`,{method:"DELETE"});notice(`VM ${name} removed. ${removeDisks?"Managed disks deleted where safe.":"Virtual disks retained."}`);await loadDashboard();}
    catch(error){notice(error.message);button.disabled=false;}return;
  }
  button.disabled=true;
  try{
    if(action==="console"){const result=await api(`/api/vms/${encodeURIComponent(name)}/console`,{method:"POST"});const detail=result.console||{};notice(detail.message||"Console launch requested on the virtualization node.");}
    else{await api(`/api/vms/${encodeURIComponent(name)}/${action}`,{method:"POST"});notice(`VM ${name}: ${action} request completed.`);await loadDashboard();}
  }catch(error){notice(error.message);button.disabled=false;}
});
async function bootstrapAuth() {
  try {
    const session = await api("/api/auth/session");
    if (!session.authenticated) {
      byId("login-gate").hidden = false;
      byId("admin-password").focus();
      return;
    }
    csrfToken = session.csrf_token;
    currentUser = session.user;
    byId("login-gate").hidden = true;
    await loadDashboard();
    await loadAdminPanel();
  } catch (error) {
    byId("login-gate").hidden = false;
    byId("login-error").textContent = error.message;
  }
}
byId("login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = byId("login-button");
  button.disabled = true;
  byId("login-error").textContent = "";
  try {
    const username = byId("admin-username").value.trim();
    const password = byId("admin-password").value;
    const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
    csrfToken = result.csrf_token;
    currentUser = result.user;
    byId("admin-password").value = "";
    byId("login-gate").hidden = true;
    await loadDashboard();
    await loadAdminPanel();
  } catch (error) {
    byId("login-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
byId("logout-button").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  csrfToken = "";
  currentUser = null;
  byId("administration").hidden = true;
  byId("admin-nav").hidden = true;
  byId("login-gate").hidden = false;
  byId("admin-password").focus();
  byId("api-status").textContent = "Signed out";
});
bootstrapAuth();
