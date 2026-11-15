const byId = id => document.getElementById(id);
const notice = message => { byId("notice").textContent = message; };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function setCreateEnabled(enabled) {
  byId("create-vm-button").disabled = !enabled;
  byId("create-vm-status").textContent = enabled
    ? "Agent connected. Review the settings before provisioning."
    : "Connect the local agent to enable provisioning.";
}

async function loadSwitches() {
  const select = byId("switch-select");
  const selected = select.value;
  const result = await api("/api/switches");
  select.replaceChildren(new Option("No switch (disconnected)", ""));
  for (const item of result.items || []) {
    const option = new Option(`${item.name} — ${item.switch_type || "virtual switch"}`, item.name);
    select.add(option);
  }
  if ([...select.options].some(option => option.value === selected)) select.value = selected;
}

function renderVMs(items) {
  const content = byId("vm-content");
  byId("vm-count").textContent = String(items.length);
  byId("vm-summary").textContent = items.length ? "VMs on connected Hyper-V host" : "No VMs found on this node";
  if (!items.length) {
    content.className = "empty";
    content.innerHTML = '<div class="empty-icon">▣</div><b>No virtual machines found</b><p class="muted">Use the form above to create your first VM.</p>';
    return;
  }
  content.className = "vm-list";
  content.innerHTML = items.map(vm => {
    const name = escapeHtml(vm.name);
    const state = escapeHtml(vm.state || "Unknown");
    const memory = Number(vm.memory_assigned_mb || 0);
    const running = String(vm.state || "").toLowerCase() === "running";
    return `<div class="vm-row"><div class="vm-ident"><b>${name}</b><span class="muted">${memory ? (memory / 1024).toFixed(1) + " GB RAM" : "Memory not reported"}</span></div><span class="state ${running ? "running" : ""}">${state}</span><div class="vm-actions"><button class="secondary" data-action="${running ? "stop" : "start"}" data-name="${name}">${running ? "Stop" : "Start"}</button></div></div>`;
  }).join("");
}

async function loadDashboard() {
  try {
    const [health, nodes, agent] = await Promise.all([
      api("/api/health"), api("/api/nodes"), api("/api/agent/status")
    ]);
    byId("api-status").textContent = health.status === "ok" ? "Control plane API online" : "API status unknown";
    byId("node-count").textContent = String(nodes.items.length);
    byId("agent-status").textContent = agent.online ? "Online" : "Offline";
    byId("agent-status").className = agent.online ? "online" : "offline";
    setCreateEnabled(agent.online);
    if (nodes.items.length) {
      byId("nodes-content").className = "node-list";
      byId("nodes-content").innerHTML = nodes.items.map(node =>
        `<div class="node-row"><b>${escapeHtml(node.name)}</b><span>${escapeHtml(node.hostname)}</span><span>${escapeHtml(node.status)}</span></div>`
      ).join("");
    }
    if (agent.online) {
      const [vms] = await Promise.all([api("/api/vms"), loadSwitches()]);
      renderVMs(vms.items || []);
    } else {
      byId("vm-count").textContent = "—";
      byId("vm-summary").textContent = "Agent not connected";
      byId("vm-content").className = "empty";
      byId("vm-content").innerHTML = `<div class="empty-icon">▣</div><b>${escapeHtml(agent.message || "Hyper-V agent is offline")}</b><p class="muted">Start the local node agent and configure VPS_AGENT_URL and VPS_AGENT_TOKEN on the API.</p>`;
    }
  } catch (error) {
    byId("api-status").textContent = "API unavailable";
    setCreateEnabled(false);
    notice(error.message);
  }
}

byId("create-vm-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const values = new FormData(form);
  const payload = {
    name: String(values.get("name")).trim(),
    iso_path: String(values.get("iso_path")).trim(),
    memory_mb: Number(values.get("memory_mb")),
    disk_gb: Number(values.get("disk_gb")),
    cpu_count: Number(values.get("cpu_count")),
    switch_name: String(values.get("switch_name") || "") || null
  };
  const summary = `${payload.name}: ${payload.cpu_count} vCPU, ${payload.memory_mb} MB RAM, ${payload.disk_gb} GB disk`;
  if (!confirm(`Create this virtual machine?\n\n${summary}\nISO: ${payload.iso_path}\n\nThe VM will be created powered off.`)) return;
  const button = byId("create-vm-button");
  button.disabled = true;
  byId("create-vm-status").textContent = "Creating VM…";
  notice("");
  try {
    await api("/api/vms", { method: "POST", body: JSON.stringify(payload) });
    byId("create-vm-status").textContent = "VM created. Refreshing inventory…";
    notice(`VM ${payload.name} was created and is powered off.`);
    form.reset();
    form.elements.memory_mb.value = "2048";
    form.elements.disk_gb.value = "20";
    form.elements.cpu_count.value = "2";
    await loadDashboard();
  } catch (error) {
    byId("create-vm-status").textContent = "Creation failed.";
    notice(error.message);
    setCreateEnabled(true);
  }
});

byId("refresh-button").addEventListener("click", loadDashboard);
byId("vm-content").addEventListener("click", async event => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const { action, name } = button.dataset;
  if (action === "stop" && !confirm(`Stop virtual machine "${name}"?`)) return;
  button.disabled = true;
  try {
    await api(`/api/vms/${encodeURIComponent(name)}/${action}`, { method: "POST" });
    notice(`VM ${name}: ${action} request completed.`);
    await loadDashboard();
  } catch (error) {
    notice(error.message);
    button.disabled = false;
  }
});
loadDashboard();
