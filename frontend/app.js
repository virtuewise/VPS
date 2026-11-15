const byId = id => document.getElementById(id);
let csrfToken = "";
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
async function loadSwitches() {
  const select = byId("switch-select"), selected = select.value, result = await api("/api/switches");
  select.replaceChildren(new Option("No switch (disconnected)", ""));
  for (const item of result.items || []) select.add(new Option(`${item.name} — ${item.switch_type || "virtual switch"}`, item.name));
  if ([...select.options].some(option => option.value === selected)) select.value = selected;
}
function renderVMs(items) {
  const content = byId("vm-content");
  byId("vm-count").textContent = String(items.length);
  byId("vm-summary").textContent = items.length ? "VMs on connected Hyper-V host" : "No VMs found on this node";
  if (!items.length) { content.className="empty"; content.innerHTML='<div class="empty-icon">▣</div><b>No virtual machines found</b><p class="muted">Use the form above to create your first VM.</p>'; return; }
  content.className="vm-list";
  content.innerHTML=items.map(vm=>{
    const name=escapeHtml(vm.name), state=escapeHtml(vm.state||"Unknown"), running=String(vm.state||"").toLowerCase()==="running";
    const net=(vm.network||[]).flatMap(n=>(n.ip_addresses||[]).map(ip=>`${escapeHtml(ip)} (${escapeHtml(n.switch_name||"unattached")})`));
    const netText=net.length?net.join(", "):"No guest IP reported yet";
    return `<div class="vm-row vm-row-expanded"><div class="vm-ident"><b>${name}</b><span class="muted">CPU ${escapeHtml(vm.cpu_usage??"—")}% · ${Number(vm.memory_assigned_mb||0)} MB assigned</span><span class="muted">Network: ${netText}</span></div><span class="state ${running?"running":""}">${state}</span><div class="vm-actions"><button class="secondary" data-action="console" data-name="${name}" ${running?"":"disabled"}>Console</button><button class="secondary" data-action="${running?"stop":"start"}" data-name="${name}">${running?"Stop":"Start"}</button><button class="danger" data-action="delete" data-name="${name}">Delete</button></div></div>`;
  }).join("");
}
async function loadMonitor() {
  const target=byId("monitor-content");
  try {
    const m=await api("/api/monitor");
    const memUsed=Math.max(0,m.memory_total_mb-m.memory_free_mb), storageUsed=Math.max(0,m.storage_total_gb-m.storage_free_gb);
    const metrics=[
      ["Host CPU",`${m.cpu_percent}%`,m.cpu_percent],
      ["Memory",`${(memUsed/1024).toFixed(1)} / ${(m.memory_total_mb/1024).toFixed(1)} GB`,m.memory_total_mb?memUsed/m.memory_total_mb*100:0],
      ["VM storage volume",`${m.storage_free_gb} GB free / ${m.storage_total_gb} GB`,m.storage_total_gb?storageUsed/m.storage_total_gb*100:0]
    ];
    target.innerHTML=metrics.map(([label,value,pct])=>`<article class="resource-card"><span class="muted">${label}</span><b>${value}</b><div class="meter"><span style="width:${Math.min(100,Math.max(0,Number(pct)||0))}%"></span></div></article>`).join("");
  } catch(error) { target.innerHTML=`<div class="empty">${escapeHtml(error.message)}</div>`; }
}
async function loadDashboard() {
  try {
    const [health,nodes,agent]=await Promise.all([api("/api/health"),api("/api/nodes"),api("/api/agent/status")]);
    byId("api-status").textContent=health.status==="ok"?"Control plane API online":"API status unknown";
    byId("node-count").textContent=String(nodes.items.length);
    byId("agent-status").textContent=agent.online?"Online":"Offline";
    byId("agent-status").className=agent.online?"online":"offline";
    setCreateEnabled(agent.online);
    if(nodes.items.length){byId("nodes-content").className="node-list";byId("nodes-content").innerHTML=nodes.items.map(n=>`<div class="node-row"><b>${escapeHtml(n.name)}</b><span>${escapeHtml(n.hostname)}</span><span>${escapeHtml(n.status)}</span></div>`).join("");}
    if(agent.online){const [vms]=await Promise.all([api("/api/vms"),loadSwitches()]);renderVMs(vms.items||[]);await loadMonitor();}
    else{byId("vm-count").textContent="—";byId("vm-summary").textContent="Agent not connected";byId("vm-content").className="empty";byId("vm-content").innerHTML=`<b>${escapeHtml(agent.message||"Hyper-V agent is offline")}</b><p class="muted">Start the local agent and configure VPS_AGENT_URL and VPS_AGENT_TOKEN.</p>`;byId("monitor-content").textContent="Node agent offline";}
  } catch(error){byId("api-status").textContent="API unavailable";setCreateEnabled(false);notice(error.message);}
}
byId("create-vm-form").addEventListener("submit",async event=>{
  event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;
  const v=new FormData(form), username=String(v.get("linux_username")||"").trim(), hash=String(v.get("password_hash")||"").trim(), key=String(v.get("ssh_public_key")||"").trim();
  const cloudFields=[username,hash,key], supplied=cloudFields.filter(Boolean).length;
  if(supplied!==0&&supplied!==3){notice("For cloud-init, fill in Linux username, SHA-512 password hash and SSH public key.");return;}
  const payload={name:String(v.get("name")).trim(),iso_path:String(v.get("iso_path")).trim(),memory_mb:Number(v.get("memory_mb")),disk_gb:Number(v.get("disk_gb")),cpu_count:Number(v.get("cpu_count")),switch_name:String(v.get("switch_name")||"")||null};
  if(supplied===3)payload.cloud_init={username,password_hash:hash,ssh_public_key:key};
  const summary=`${payload.name}: ${payload.cpu_count} vCPU, ${payload.memory_mb} MB RAM, ${payload.disk_gb} GB disk`;
  if(!confirm(`Create this VM?\n\n${summary}\nCloud-init: ${supplied===3?"enabled":"not enabled"}\n\nIt will be created powered off.`))return;
  const button=byId("create-vm-button");button.disabled=true;byId("create-vm-status").textContent="Creating VM…";notice("");
  try{await api("/api/vms",{method:"POST",body:JSON.stringify(payload)});notice(`VM ${payload.name} created powered off.`);form.reset();form.elements.memory_mb.value="2048";form.elements.disk_gb.value="20";form.elements.cpu_count.value="2";await loadDashboard();}
  catch(error){byId("create-vm-status").textContent="Creation failed.";notice(error.message);setCreateEnabled(true);}
});
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
    if(action==="console"){await api(`/api/vms/${encodeURIComponent(name)}/console`,{method:"POST"});notice("Hyper-V console launch requested on the Windows node. The console window opens on that machine.");}
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
    byId("login-gate").hidden = true;
    await loadDashboard();
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
    const password = byId("admin-password").value;
    const result = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
    csrfToken = result.csrf_token;
    byId("admin-password").value = "";
    byId("login-gate").hidden = true;
    await loadDashboard();
  } catch (error) {
    byId("login-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
byId("logout-button").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  csrfToken = "";
  byId("login-gate").hidden = false;
  byId("admin-password").focus();
  byId("api-status").textContent = "Signed out";
});
bootstrapAuth();
