"use strict";

const API_BASE = "/api/v1";

const state = {
  analysisId: null,
  graph: null,
  findingsByComponent: new Map(),
  selectedNodeId: null,
  selectionRequest: 0,
  filters: { search: "", type: "", securityOnly: false },
  securityFocus: false,
  workspaceTab: "overview",
  analysisRunning: false,
  viewMode: "topology",
  labelsVisible: true,
  hoverNodeId: null,
  securityHighOnly: false,
};

async function apiPost(path, body) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail ?? JSON.stringify(payload);
    } catch (_err) {}
    throw new Error(`${path} failed (${response.status}): ${detail}`);
  }
  return response.json();
}

const analyzeRepo = (repoUrl) => apiPost("/analyze", { repo_url: repoUrl });
const fetchSecurityFindings = (analysisId) => apiPost("/security", { analysis_id: analysisId });
const explainNode = (analysisId, nodeId) => apiPost("/explain", { analysis_id: analysisId, node_id: nodeId });
const impactOfNode = (analysisId, nodeId) => apiPost("/impact", { analysis_id: analysisId, node_id: nodeId });
const dependenciesOfNode = (analysisId, nodeId) => apiPost("/dependencies", { analysis_id: analysisId, node_id: nodeId });

// Kept as a pure, cycle-safe helper for frontend regression tests and as a
// deterministic fallback for any future 2D view.
function computeLayout(nodes, edges, opts = {}) {
  const nodeSpacingX = opts.nodeSpacingX ?? 220;
  const nodeSpacingY = opts.nodeSpacingY ?? 90;
  const ids = nodes.map((n) => n.id);
  const indegree = new Map(ids.map((id) => [id, 0]));
  const outgoing = new Map(ids.map((id) => [id, []]));
  for (const e of edges) {
    if (!outgoing.has(e.source) || !indegree.has(e.target)) continue;
    outgoing.get(e.source).push(e.target);
    indegree.set(e.target, indegree.get(e.target) + 1);
  }
  const remaining = new Map(indegree);
  let frontier = ids.filter((id) => remaining.get(id) === 0);
  const levels = new Map();
  const visited = new Set();
  let level = 0;
  while (frontier.length) {
    for (const id of frontier) { levels.set(id, level); visited.add(id); }
    const next = [];
    for (const id of frontier) {
      for (const target of outgoing.get(id)) {
        if (visited.has(target)) continue;
        remaining.set(target, remaining.get(target) - 1);
        if (remaining.get(target) === 0) next.push(target);
      }
    }
    frontier = [...new Set(next)];
    level += 1;
  }
  for (const id of ids) if (!visited.has(id)) levels.set(id, level);
  const byLevel = new Map();
  for (const id of ids) {
    const lvl = levels.get(id);
    if (!byLevel.has(lvl)) byLevel.set(lvl, []);
    byLevel.get(lvl).push(id);
  }
  const positions = {};
  for (const [lvl, idsAtLevel] of byLevel) {
    idsAtLevel.forEach((id, index) => {
      positions[id] = { x: lvl * nodeSpacingX + 80, y: index * nodeSpacingY + 60 };
    });
  }
  return positions;
}

function getFilteredGraph() {
  if (!state.graph) return { nodes: [], edges: [] };
  const query = state.filters.search.toLowerCase();
  const visibleNodes = state.graph.nodes.filter((node) => {
    const matchesSearch = !query || [node.name, node.technology, node.node_type, node.id]
      .some((value) => String(value ?? "").toLowerCase().includes(query));
    const matchesType = !state.filters.type || node.node_type === state.filters.type;
    const matchesSecurity = !state.filters.securityOnly || state.findingsByComponent.has(node.id);
    const matchesFocus = !state.securityFocus || state.findingsByComponent.has(node.id) || relationshipEntries(node.id).some(({ other }) => state.findingsByComponent.has(other.id));
    return matchesSearch && matchesType && matchesSecurity && matchesFocus;
  });
  const ids = new Set(visibleNodes.map((node) => node.id));
  return {
    nodes: visibleNodes,
    edges: state.graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  };
}

function populateTypeFilter() {
  const select = document.getElementById("node-type-filter");
  const types = [...new Set((state.graph?.nodes ?? []).map((node) => node.node_type).filter(Boolean))].sort();
  select.innerHTML = '<option value="">All types</option>';
  for (const type of types) {
    const option = document.createElement("option");
    option.value = type;
    option.textContent = type;
    select.appendChild(option);
  }
}

function renderGraph(graph = getFilteredGraph()) {
  if (!window.infraLens3D) {
    document.getElementById("graph-summary").textContent = "3D viewer is still loading...";
    return;
  }
  window.infraLens3D.render(graph, state.findingsByComponent, selectNode, (nodeId) => { state.hoverNodeId = nodeId; renderGraphHover(nodeId); });
  window.infraLens3D.setViewMode?.(state.viewMode);
  const totalFindings = [...state.findingsByComponent.values()].reduce((sum, list) => sum + list.length, 0);
  const filterText = (state.filters.search || state.filters.type || state.filters.securityOnly) ? " - filtered" : "";
  document.getElementById("graph-summary").textContent =
    `${graph.nodes.length} of ${state.graph.nodes.length} components - ${graph.edges.length} relationships` +
    (totalFindings ? ` - ${totalFindings} security finding${totalFindings === 1 ? "" : "s"}` : "") + filterText;
  document.getElementById("graph-node-count").textContent = `${graph.nodes.length} node${graph.nodes.length === 1 ? "" : "s"}`;
  document.getElementById("graph-edge-count").textContent = `${graph.edges.length} edge${graph.edges.length === 1 ? "" : "s"}`;
  document.getElementById("graph-view-label").textContent = state.viewMode.charAt(0).toUpperCase() + state.viewMode.slice(1);
}

function setupGraphControls() {
  const search = document.getElementById("node-search");
  const type = document.getElementById("node-type-filter");
  const securityOnly = document.getElementById("security-only");
  const rerender = () => {
    state.filters.search = search.value.trim();
    state.filters.type = type.value;
    state.filters.securityOnly = securityOnly.checked;
    renderGraph();
  };
  search.addEventListener("input", rerender);
  type.addEventListener("change", rerender);
  securityOnly.addEventListener("change", rerender);
  document.getElementById("graph-view-mode").addEventListener("change", (event) => {
    state.viewMode = event.target.value;
    window.infraLens3D?.setViewMode(state.viewMode);
    renderGraph();
  });
  document.getElementById("fit-graph").addEventListener("click", () => window.infraLens3D?.fitView());
  document.getElementById("focus-selected").addEventListener("click", () => {
    if (state.selectedNodeId) window.infraLens3D?.focusNode(state.selectedNodeId);
  });
  document.getElementById("clear-selection").addEventListener("click", clearNodeSelection);
  document.getElementById("reset-camera").addEventListener("click", () => window.infraLens3D?.resetView());
  document.getElementById("toggle-fullscreen").addEventListener("click", toggleGraphFullscreen);
}


function clearNodeSelection() {
  state.selectedNodeId = null;
  state.selectionRequest += 1;
  document.getElementById("detail-empty").hidden = false;
  document.getElementById("detail-content").hidden = true;
  window.infraLens3D?.clearSelection();
}

function toggleGraphFullscreen() {
  const target = document.getElementById("graph-3d");
  if (!document.fullscreenElement) {
    target.requestFullscreen?.();
  } else {
    document.exitFullscreen?.();
  }
}

function renderGraphHover(nodeId) {
  const card = document.getElementById("graph-hover-card");
  if (!nodeId) { card.hidden = true; return; }
  const node = state.graph?.nodes.find((item) => item.id === nodeId);
  if (!node) { card.hidden = true; return; }
  const findingCount = (state.findingsByComponent.get(nodeId) || []).length;
  card.innerHTML = `<strong>${escapeHtml(node.name)}</strong><span>${escapeHtml(node.node_type)} - ${escapeHtml(node.technology)}</span>${findingCount ? `<b>${findingCount} security finding${findingCount === 1 ? "" : "s"}</b>` : ""}`;
  card.hidden = false;
}


function securitySummary() {
  const findings = [...state.findingsByComponent.values()].flat();
  const count = (severity) => findings.filter((finding) => String(finding.severity).toLowerCase() === severity).length;
  return { total: findings.length, high: count("high"), medium: count("medium"), low: count("low") };
}

function renderSecurityOverview() {
  const summary = securitySummary();
  document.getElementById("security-total").textContent = String(summary.total);
  document.getElementById("security-high").textContent = String(summary.high);
  document.getElementById("security-medium").textContent = String(summary.medium);
  document.getElementById("security-low").textContent = String(summary.low);
  const button = document.getElementById("security-focus");
  button.classList.toggle("active", state.securityFocus);
  button.setAttribute("aria-pressed", String(state.securityFocus));
  button.textContent = state.securityFocus ? "Show full map" : "Show attack surface";
}

function setupSecurityOverview() {
  document.getElementById("security-focus").addEventListener("click", () => {
    state.securityFocus = !state.securityFocus;
    renderSecurityOverview();
    renderGraph();
    if (state.securityFocus) {
      const firstFinding = [...state.findingsByComponent.keys()][0];
      if (firstFinding) selectNode(firstFinding);
    }
  });
}

function relationshipEntries(nodeId) {
  if (!state.graph) return [];
  const nodes = new Map(state.graph.nodes.map((node) => [node.id, node]));
  return state.graph.edges
    .filter((edge) => edge.source === nodeId || edge.target === nodeId)
    .map((edge) => {
      const outgoing = edge.source === nodeId;
      const other = nodes.get(outgoing ? edge.target : edge.source);
      return { edge, outgoing, other };
    })
    .filter((entry) => entry.other);
}

function safeMetadataEntries(node) {
  const metadata = node?.metadata ?? {};
  const blocked = /(secret|password|token|api[_-]?key|credential|private[_-]?key)/i;
  return Object.entries(metadata)
    .filter(([key, value]) => value !== null && value !== undefined && !blocked.test(key))
    .slice(0, 12)
    .map(([key, value]) => {
      let display = value;
      if (Array.isArray(value)) display = value.join(", ");
      else if (typeof value === "object") display = JSON.stringify(value);
      return [key, String(display)];
    });
}

function renderComponentOverview(nodeId) {
  const node = state.graph?.nodes.find((item) => item.id === nodeId);
  if (!node) return;

  const identity = document.getElementById("component-identity");
  identity.innerHTML = "";
  for (const [label, value] of [
    ["Type", node.node_type],
    ["Technology", node.technology],
    ["ID", node.id],
  ]) {
    const item = document.createElement("div");
    item.className = "identity-item";
    item.innerHTML = `<span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong>`;
    identity.appendChild(item);
  }

  const metadata = document.getElementById("component-metadata");
  metadata.innerHTML = "";
  const entries = safeMetadataEntries(node);
  if (!entries.length) {
    metadata.innerHTML = '<div class="muted-note">No safe display metadata available.</div>';
  } else {
    for (const [key, value] of entries) {
      const row = document.createElement("div");
      row.className = "metadata-row";
      row.innerHTML = `<span>${escapeHtml(key)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong>`;
      metadata.appendChild(row);
    }
  }

  const relationships = document.getElementById("component-relationships");
  relationships.innerHTML = "";
  const entriesByEdge = relationshipEntries(nodeId);
  if (!entriesByEdge.length) {
    relationships.innerHTML = '<div class="muted-note">No graph relationships found.</div>';
    return;
  }
  for (const { edge, outgoing, other } of entriesByEdge) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "relationship-item";
    const origin = edge.metadata?.origin ?? "unknown";
    const direction = outgoing ? "->" : "<-";
    const evidence = origin === "inferred"
      ? `inferred${edge.metadata?.confidence ? ` - ${edge.metadata.confidence}` : ""}`
      : "parsed";
    item.innerHTML = `
      <span class="relationship-main">
        <span class="relationship-direction">${direction}</span>
        <span class="relationship-name">${escapeHtml(other.name)}</span>
        <span class="relationship-type">${escapeHtml(edge.edge_type)}</span>
      </span>
      <span class="relationship-evidence">${escapeHtml(evidence)}${edge.metadata?.basis ? ` - ${escapeHtml(edge.metadata.basis)}` : ""}</span>`;
    item.addEventListener("click", () => selectNode(other.id));
    relationships.appendChild(item);
  }
}

async function selectNode(nodeId) {
  const requestId = ++state.selectionRequest;
  state.selectedNodeId = nodeId;
  state.hoverNodeId = null;
  renderGraphHover(null);
  if (window.infraLens3D) window.infraLens3D.select(nodeId);

  const node = state.graph?.nodes.find((n) => n.id === nodeId);
  if (!node) return;

  document.getElementById("detail-empty").hidden = true;
  document.getElementById("detail-content").hidden = false;
  document.getElementById("detail-name").textContent = node.name;
  document.getElementById("detail-meta").textContent = `${node.technology} - ${node.node_type}`;
  renderComponentOverview(nodeId);

  setTabLoading("explain");
  setTabLoading("impact");
  setTabLoading("dependencies");
  renderSecurityTab(nodeId);

  const guarded = (promise, tab, renderer) => promise
    .then((result) => {
      if (requestId === state.selectionRequest) renderer(result);
    })
    .catch((err) => {
      if (requestId === state.selectionRequest) renderTabError(tab, err);
    });

  await Promise.all([
    guarded(explainNode(state.analysisId, nodeId), "explain", renderExplainTab),
    guarded(impactOfNode(state.analysisId, nodeId), "impact", renderImpactTab),
    guarded(dependenciesOfNode(state.analysisId, nodeId), "dependencies", renderDependenciesTab),
  ]);
}

function setTabLoading(tab) { document.getElementById(`${tab}-content`).textContent = "Loading..."; }
function renderTabError(tab, err) { document.getElementById(`${tab}-content`).textContent = `Could not load: ${err.message}`; }

function renderExplainTab(result) {
  const el = document.getElementById("explain-content");
  el.innerHTML = `<p>${escapeHtml(result.explanation)}</p>`;
  const badge = document.createElement("span");
  badge.className = "confidence-badge";
  badge.textContent = `confidence: ${result.confidence} - ${result.generation_method}`;
  el.appendChild(badge);
}

function renderImpactTab(report) {
  const el = document.getElementById("impact-content");
  el.innerHTML = "";
  const summary = document.createElement("p");
  summary.textContent = `${report.total_impact_count} component(s) would be affected: ${report.direct_dependents.length} directly, ${report.transitive_dependents.length} transitively.`;
  el.appendChild(summary);
  el.appendChild(renderNodeList("Direct dependents", report.direct_dependents));
  el.appendChild(renderNodeList("Transitive dependents", report.transitive_dependents));
}

function renderDependenciesTab(result) {
  const el = document.getElementById("dependencies-content");
  el.innerHTML = "";
  el.appendChild(renderNodeList("Depends on", result.dependencies));
  el.appendChild(renderNodeList("Depended on by", result.dependents));
}

function renderNodeList(title, nodes) {
  const wrapper = document.createElement("div");
  const heading = document.createElement("p");
  heading.textContent = `${title} (${nodes.length})`;
  wrapper.appendChild(heading);
  if (!nodes.length) return wrapper;
  const list = document.createElement("div");
  list.className = "rel-list";
  for (const n of nodes) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "analysis-link";
    button.innerHTML = `<span><strong>${escapeHtml(n.name)}</strong><small>${escapeHtml(n.node_type)}</small></span><b>Focus</b>`;
    button.addEventListener("click", () => {
      showWorkspaceTab("map");
      selectNode(n.id);
    });
    list.appendChild(button);
  }
  wrapper.appendChild(list);
  return wrapper;
}

function renderSecurityTab(nodeId) {
  const el = document.getElementById("security-content");
  el.innerHTML = "";
  const findings = state.findingsByComponent.get(nodeId) ?? [];
  if (!findings.length) {
    el.innerHTML = "<p>No security findings for this component.</p>";
    return;
  }
  for (const finding of findings) {
    const card = document.createElement("div");
    card.className = "finding";
    card.innerHTML = `<div class="finding-title">${escapeHtml(finding.title)} <span class="severity-badge severity-${escapeHtml(finding.severity)}">${escapeHtml(finding.severity)}</span></div><div>${escapeHtml(finding.reason)}</div>`;
    if (finding.remediation) {
      const remediation = document.createElement("div");
      remediation.className = "remediation";
      remediation.textContent = `Remediation: ${finding.remediation}`;
      card.appendChild(remediation);
    }
    el.appendChild(card);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
}

function setupTabs() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.tab;
      document.querySelectorAll(".tab-button").forEach((b) => b.classList.toggle("active", b === button));
      document.querySelectorAll(".tab-panel").forEach((panel) => { panel.hidden = panel.dataset.tabPanel !== tab; });
    });
  });
}

function setStatus(message, isError = false) {
  const el = document.getElementById("status");
  const text = document.getElementById("status-text");
  if (text) text.textContent = message;
  else el.textContent = message;
  el.classList.toggle("error", isError);
  el.classList.toggle("success", !isError && /complete|ready/i.test(message));
}

function showToast(message, kind = "info") {
  const toast = document.getElementById("global-toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = `global-toast ${kind}`;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 2600);
}

function setProgress(step, title, message) {
  const labels = { 1: "Scanning repository", 2: "Building infrastructure model", 3: "Checking security posture", 4: "Workspace ready" };
  const progress = Math.min(step * 25, 100);
  document.getElementById("progress-percent").textContent = `${progress}%`;
  document.getElementById("progress-title").textContent = title || labels[step];
  document.getElementById("progress-message").textContent = message || "Working through the analysis pipeline...";
  document.getElementById("progress-bar").style.width = `${progress}%`;
  ["clone", "model", "security", "ready"].forEach((name, index) => {
    const el = document.getElementById(`progress-step-${name}`);
    el.classList.toggle("active", index < step);
    el.classList.toggle("current", index === step - 1);
  });
}

function showView(id) {
  ["landing-view", "analysis-progress", "analysis-error", "workspace-view"].forEach((viewId) => {
    const el = document.getElementById(viewId);
    el.hidden = viewId !== id;
  });
}


function renderGraphSignals() {
  const nodes = state.graph?.nodes ?? [];
  const edges = state.graph?.edges ?? [];
  const incoming = new Map(nodes.map((node) => [node.id, 0]));
  const outgoing = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of edges) {
    if (incoming.has(edge.target)) incoming.set(edge.target, incoming.get(edge.target) + 1);
    if (outgoing.has(edge.source)) outgoing.set(edge.source, outgoing.get(edge.source) + 1);
  }
  const entries = nodes.filter((node) => incoming.get(node.id) === 0);
  const terminals = nodes.filter((node) => outgoing.get(node.id) === 0);
  const isolated = nodes.filter((node) => incoming.get(node.id) === 0 && outgoing.get(node.id) === 0);
  const hotspot = nodes.slice().sort((a, b) => {
    const scoreA = incoming.get(a.id) + outgoing.get(a.id);
    const scoreB = incoming.get(b.id) + outgoing.get(b.id);
    return scoreB - scoreA || a.name.localeCompare(b.name);
  })[0];
  document.getElementById("signal-entry-nodes").textContent = String(entries.length);
  document.getElementById("signal-terminal-nodes").textContent = String(terminals.length);
  document.getElementById("signal-isolated-nodes").textContent = String(isolated.length);
  document.getElementById("signal-hotspot").textContent = hotspot ? hotspot.name : "-";
}

function renderWorkspaceMetrics(repository) {
  const nodes = state.graph?.nodes ?? [];
  const edges = state.graph?.edges ?? [];
  const summary = securitySummary();
  document.getElementById("workspace-repository").textContent = repository || "Repository architecture";
  document.getElementById("completion-title").textContent = `${repository || "Repository"} is mapped`;
  document.getElementById("completion-summary").textContent = `${nodes.length} infrastructure components and ${edges.length} relationships are ready to explore.`;
  document.getElementById("metric-components").textContent = String(nodes.length);
  document.getElementById("metric-relationships").textContent = String(edges.length);
  document.getElementById("metric-findings").textContent = String(summary.total);
  document.getElementById("metric-high").textContent = String(summary.high);
  document.getElementById("analysis-id-display").textContent = state.analysisId || "-";
  document.getElementById("workspace-analysis-meta").textContent = state.analysisId ? `Session ${state.analysisId}` : "Analysis session ready";
  renderGraphSignals();
}

function securityFindings() {
  return [...state.findingsByComponent.values()].flat();
}

function attackSurfaceFindings() {
  return securityFindings().filter((finding) => String(finding.rule_id || "").toUpperCase() === "EXPOSED_PATH_TO_SECRET");
}

function severityWeight(value) {
  return { high: 0, medium: 1, low: 2 }[String(value || "").toLowerCase()] ?? 9;
}

function renderSecurityPostureSummary() {
  const el = document.getElementById("security-posture-summary");
  const findings = securityFindings();
  const paths = attackSurfaceFindings();
  const summary = securitySummary();
  const affected = new Set(findings.map((finding) => finding.component_id).filter(Boolean)).size;
  const posture = summary.high > 0 ? "Attention required" : summary.total > 0 ? "Review recommended" : "No findings detected";
  el.innerHTML = `
    <div class="posture-main"><span class="eyebrow">CURRENT POSTURE</span><strong>${escapeHtml(posture)}</strong><p>${findings.length ? `${affected} component${affected === 1 ? "" : "s"} affected across ${findings.length} finding${findings.length === 1 ? "" : "s"}.` : "The current analysis returned no deterministic security findings."}</p></div>
    <div class="posture-stat"><span>HIGH</span><strong>${summary.high}</strong></div>
    <div class="posture-stat"><span>MEDIUM</span><strong>${summary.medium}</strong></div>
    <div class="posture-stat"><span>LOW</span><strong>${summary.low}</strong></div>
    <div class="posture-stat attack"><span>ATTACK PATHS</span><strong>${paths.length}</strong></div>`;
}

function renderAttackSurface() {
  const container = document.getElementById("attack-path-list");
  const count = document.getElementById("attack-path-count");
  const nodeMap = new Map((state.graph?.nodes ?? []).map((node) => [node.id, node]));
  const paths = attackSurfaceFindings();
  count.textContent = `${paths.length} path${paths.length === 1 ? "" : "s"}`;
  container.innerHTML = "";
  if (!paths.length) {
    container.innerHTML = '<div class="security-empty compact"><span class="security-empty-icon">OK</span><h4>No exposed path to a secret</h4><p>No EXPOSED_PATH_TO_SECRET finding was returned for this analysis.</p></div>';
    return;
  }
  for (const finding of paths) {
    const card = document.createElement("article");
    card.className = "attack-path-item";
    const node = nodeMap.get(finding.component_id);
    card.innerHTML = `<div class="attack-path-top"><span class="severity-badge severity-high">HIGH</span><span>${escapeHtml(finding.rule_id || "EXPOSED_PATH_TO_SECRET")}</span></div><h4>${escapeHtml(finding.title || "Exposed path to secret")}</h4><p>${escapeHtml(finding.reason || "An externally reachable component has a graph path to sensitive infrastructure.")}</p><div class="attack-path-component"><span>AFFECTED</span><strong>${escapeHtml(node?.name || finding.component_id || "Unknown component")}</strong></div>`;
    card.addEventListener("click", () => {
      showWorkspaceTab("map");
      state.securityFocus = true;
      renderSecurityOverview();
      renderGraph();
      if (finding.component_id) selectNode(finding.component_id);
      window.infraLens3D?.focusNode(finding.component_id);
    });
    container.appendChild(card);
  }
}

function renderSecurityPriorities() {
  const container = document.getElementById("security-priority-list");
  const findings = securityFindings().slice().sort((a, b) => severityWeight(a.severity) - severityWeight(b.severity));
  const nodeMap = new Map((state.graph?.nodes ?? []).map((node) => [node.id, node]));
  container.innerHTML = "";
  if (!findings.length) {
    container.innerHTML = '<div class="mini-empty">No remediation priorities are present for this analysis.</div>';
    return;
  }
  const seen = new Set();
  for (const finding of findings) {
    const key = `${finding.rule_id}:${finding.component_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const row = document.createElement("button");
    row.type = "button";
    row.className = "security-priority-item";
    const node = nodeMap.get(finding.component_id);
    const severity = String(finding.severity || "unknown").toLowerCase();
    row.innerHTML = `<span class="priority-severity severity-${escapeHtml(severity)}">${escapeHtml(severity)}</span><span class="priority-copy"><strong>${escapeHtml(finding.title || "Security finding")}</strong><small>${escapeHtml(node?.name || finding.component_id || "Unknown component")}</small></span><span class="jump">Inspect</span>`;
    row.addEventListener("click", () => {
      showWorkspaceTab("map");
      if (finding.component_id) selectNode(finding.component_id);
    });
    container.appendChild(row);
    if (seen.size >= 5) break;
  }
}

function renderSecurityPage() {
  renderSecurityPostureSummary();
  renderAttackSurface();
  renderSecurityPriorities();
  const container = document.getElementById("security-findings-list");
  const countEl = document.getElementById("security-findings-count");
  const highButton = document.getElementById("security-show-high");
  container.innerHTML = "";
  const nodeMap = new Map((state.graph?.nodes ?? []).map((node) => [node.id, node]));
  let findings = securityFindings().slice().sort((a, b) => severityWeight(a.severity) - severityWeight(b.severity));
  if (state.securityHighOnly) findings = findings.filter((finding) => String(finding.severity).toLowerCase() === "high");
  countEl.textContent = `${findings.length} finding${findings.length === 1 ? "" : "s"}`;
  highButton.setAttribute("aria-pressed", String(state.securityHighOnly));
  highButton.classList.toggle("active", state.securityHighOnly);
  if (!findings.length) {
    container.innerHTML = state.securityHighOnly
      ? '<div class="security-empty"><span class="security-empty-icon">OK</span><h3>No high severity findings</h3><p>There are no high severity findings in the current analysis.</p></div>'
      : '<div class="security-empty"><span class="security-empty-icon">OK</span><h3>No security findings</h3><p>No deterministic findings were returned for this analysis.</p></div>';
    return;
  }
  for (const finding of findings) {
    const node = nodeMap.get(finding.component_id);
    const card = document.createElement("article");
    card.className = "security-page-card";
    const severity = String(finding.severity || "unknown").toLowerCase();
    const isPath = String(finding.rule_id || "").toUpperCase() === "EXPOSED_PATH_TO_SECRET";
    card.innerHTML = `<div class="security-page-card-head"><span class="severity-badge severity-${escapeHtml(severity)}">${escapeHtml(severity)}</span><span class="security-rule">${escapeHtml(finding.rule_id || "Finding")}</span>${isPath ? '<span class="attack-badge">ATTACK PATH</span>' : ""}</div><h4>${escapeHtml(finding.title || "Security finding")}</h4><p>${escapeHtml(finding.reason || "")}</p><button type="button" class="security-component-button"><span>AFFECTED COMPONENT</span><strong>${escapeHtml(node?.name || finding.component_id || "Unknown component")}</strong><b>Inspect in map</b></button>`;
    const componentButton = card.querySelector(".security-component-button");
    componentButton.addEventListener("click", (event) => {
      event.stopPropagation();
      showWorkspaceTab("map");
      state.securityFocus = isPath || state.securityFocus;
      renderSecurityOverview();
      renderGraph();
      if (finding.component_id) selectNode(finding.component_id);
    });
    if (finding.remediation) {
      const remediation = document.createElement("div");
      remediation.className = "remediation";
      remediation.textContent = `Remediation: ${finding.remediation}`;
      card.appendChild(remediation);
    }
    card.addEventListener("click", () => {
      if (finding.component_id) selectNode(finding.component_id);
    });
    container.appendChild(card);
  }
}

function showWorkspaceTab(tab) {
  state.workspaceTab = tab;
  document.querySelectorAll(".workspace-tab").forEach((button) => button.classList.toggle("active", button.dataset.workspaceTab === tab));
  document.querySelectorAll(".workspace-section").forEach((section) => {
    const visible = section.id === `${tab}-section`;
    section.hidden = !visible;
  });
  if (tab === "security") renderSecurityPage();
  if (tab === "map") setTimeout(() => window.infraLens3D?.fitView(), 0);
}

function setupWorkspaceNavigation() {
  document.querySelectorAll(".workspace-tab").forEach((button) => {
    button.addEventListener("click", () => showWorkspaceTab(button.dataset.workspaceTab));
  });
  document.getElementById("open-map").addEventListener("click", () => showWorkspaceTab("map"));
  document.getElementById("security-show-high").addEventListener("click", () => {
    state.securityHighOnly = !state.securityHighOnly;
    renderSecurityPage();
  });
  document.getElementById("security-open-map").addEventListener("click", () => {
    showWorkspaceTab("map");
    state.securityFocus = true;
    renderSecurityOverview();
    renderGraph();
    const firstAttack = attackSurfaceFindings()[0];
    const firstFinding = firstAttack?.component_id || [...state.findingsByComponent.keys()][0];
    if (firstFinding) selectNode(firstFinding);
  });
}

function resetWorkspaceForNewAnalysis() {
  state.graph = null;
  state.analysisId = null;
  state.selectedNodeId = null;
  state.findingsByComponent = new Map();
  state.filters = { search: "", type: "", securityOnly: false };
  state.securityFocus = false;
  state.viewMode = "topology";
  state.labelsVisible = true;
  state.hoverNodeId = null;
  state.securityHighOnly = false;
}

async function handleAnalyzeSubmit(event) {
  event.preventDefault();
  const repoUrl = document.getElementById("repo-url-input").value.trim();
  if (!repoUrl || state.analysisRunning) return;

  const button = document.getElementById("analyze-button");
  state.analysisRunning = true;
  button.disabled = true;
  button.textContent = "Analyzing...";
  resetWorkspaceForNewAnalysis();
  document.getElementById("app").setAttribute("aria-busy", "true");
  showView("analysis-progress");
  setProgress(1, "Scanning repository", "Cloning the repository and detecting infrastructure files.");
  setStatus("Analysis in progress...");

  try {
    const analyzeResponse = await analyzeRepo(repoUrl);
    state.analysisId = analyzeResponse.analysis_id;
    state.graph = analyzeResponse.graph;
    state.selectedNodeId = null;
    setProgress(2, "Building infrastructure model", "Normalizing components and relationships into the infrastructure graph.");

    setStatus("Loading security findings...");
    setProgress(3, "Checking security posture", "Evaluating the modeled infrastructure for deterministic security findings.");
    const securityResponse = await fetchSecurityFindings(state.analysisId);
    state.findingsByComponent = new Map();
    for (const finding of securityResponse.findings) {
      const list = state.findingsByComponent.get(finding.component_id) ?? [];
      list.push(finding);
      state.findingsByComponent.set(finding.component_id, list);
    }

    document.getElementById("graph-title").textContent = `${analyzeResponse.repository} architecture`;
    state.filters = { search: "", type: "", securityOnly: false };
    document.getElementById("node-search").value = "";
    document.getElementById("security-only").checked = false;
    populateTypeFilter();
    state.securityFocus = false;
    renderSecurityOverview();
    renderGraph();
    renderWorkspaceMetrics(analyzeResponse.repository);
    renderSecurityPage();
    setProgress(4, "Workspace ready", "Your infrastructure model is ready to explore.");
    showView("workspace-view");
    showWorkspaceTab("overview");
    setStatus(`Analysis complete - ${state.graph.nodes.length} components - ${state.graph.edges.length} relationships`);
    showToast("Analysis ready. Explore the infrastructure map.", "success");
  } catch (err) {
    const message = err.message || "Analysis failed. Try again.";
    document.getElementById("analysis-error-message").textContent = message;
    document.getElementById("analysis-error-title").textContent = "We could not complete this analysis";
    showView("analysis-error");
    setStatus(message, true);
    showToast("Analysis failed. You can retry without losing the URL.", "error");
  } finally {
    document.getElementById("app").setAttribute("aria-busy", "false");
    state.analysisRunning = false;
    button.disabled = false;
    button.textContent = "Analyze repository";
  }
}

function init() {
  document.getElementById("analyze-form").addEventListener("submit", handleAnalyzeSubmit);
  document.getElementById("analysis-error-retry").addEventListener("click", () => {
    document.getElementById("analyze-form").requestSubmit();
  });
  document.getElementById("analysis-error-home").addEventListener("click", () => {
    showView("landing-view");
    setStatus("Ready");
  });
  setupTabs();
  setupGraphControls();
  window.infraLens3D?.setViewMode?.(state.viewMode);
  setupSecurityOverview();
  setupWorkspaceNavigation();
  showView("landing-view");
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { computeLayout };
} else {
  document.addEventListener("DOMContentLoaded", init);
}
