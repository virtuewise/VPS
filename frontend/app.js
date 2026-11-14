async function loadDashboard() {
  const status = document.getElementById("api-status");
  const count = document.getElementById("node-count");
  const content = document.getElementById("nodes-content");
  try {
    const [healthResponse, nodesResponse] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/nodes")
    ]);
    if (!healthResponse.ok || !nodesResponse.ok) throw new Error("API request failed");
    const health = await healthResponse.json();
    const nodes = await nodesResponse.json();
    status.textContent = health.status === "ok" ? "API responding normally" : "API status unknown";
    count.textContent = String(nodes.items.length);
    if (nodes.items.length) {
      content.className = "";
      content.innerHTML = nodes.items.map(node =>
        `<div class="node-row"><b>${escapeHtml(node.name)}</b><span>${escapeHtml(node.hostname)}</span><span>${escapeHtml(node.status)}</span></div>`
      ).join("");
    }
  } catch (error) {
    status.textContent = "API unavailable";
    content.textContent = "Could not load compute nodes. Check that the API is running.";
  }
}
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}
loadDashboard();
