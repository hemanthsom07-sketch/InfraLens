"use strict";

/* InfraLens frontend (Phase 8).
 *
 * Deliberately dependency-free: no build step, no CDN script, no npm
 * package — plain HTML/CSS/JS served as static files by FastAPI. This
 * consumes the existing, unchanged Phase 7A-7G APIs exactly as they are;
 * it introduces no new backend logic of its own. The one piece of real
 * client-side logic (computeLayout) is a small, cycle-safe layered graph
 * layout, chosen specifically because it needs no external library.
 */

const API_BASE = "/api/v1";

const state = {
  analysisId: null,
  graph: null, // { nodes, edges, metadata }
  findingsByComponent: new Map(), // component_id -> [SecurityFinding]
  selectedNodeId: null,
  nodePositions: {}, // id -> {x, y}
};

// --- API calls ---------------------------------------------------------------

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
    } catch (_err) {
      /* response body wasn't JSON -- fall back to statusText above */
    }
    throw new Error(`${path} failed (${response.status}): ${detail}`);
  }
  return response.json();
}

function analyzeRepo(repoUrl) {
  return apiPost("/analyze", { repo_url: repoUrl });
}

function fetchSecurityFindings(analysisId) {
  return apiPost("/security", { analysis_id: analysisId });
}

function explainNode(analysisId, nodeId) {
  return apiPost("/explain", { analysis_id: analysisId, node_id: nodeId });
}

function impactOfNode(analysisId, nodeId) {
  return apiPost("/impact", { analysis_id: analysisId, node_id: nodeId });
}

function dependenciesOfNode(analysisId, nodeId) {
  return apiPost("/dependencies", { analysis_id: analysisId, node_id: nodeId });
}

// --- Layout (pure function, no DOM access -- see /tmp/layout_test.js style
// coverage; this is the exact algorithm verified before being embedded here)

function computeLayout(nodes, edges, opts = {}) {
  const nodeSpacingX = opts.nodeSpacingX ?? 220;
  const nodeSpacingY = opts.nodeSpacingY ?? 90;

  const ids = nodes.map((n) => n.id);
  const indegree = new Map(ids.map((id) => [id, 0]));
  const outgoing = new Map(ids.map((id) => [id, []]));
  for (const e of edges) {
    if (!outgoing.has(e.source) || !indegree.has(e.target)) continue; // defensive: ignore dangling refs
    outgoing.get(e.source).push(e.target);
    indegree.set(e.target, indegree.get(e.target) + 1);
  }

  const remainingIndegree = new Map(indegree);
  let frontier = ids.filter((id) => remainingIndegree.get(id) === 0);
  const levels = new Map();
  let level = 0;
  const visited = new Set();

  while (frontier.length > 0) {
    for (const id of frontier) {
      levels.set(id, level);
      visited.add(id);
    }
    const next = [];
    for (const id of frontier) {
      for (const target of outgoing.get(id)) {
        if (visited.has(target)) continue;
        remainingIndegree.set(target, remainingIndegree.get(target) - 1);
        if (remainingIndegree.get(target) === 0) next.push(target);
      }
    }
    frontier = [...new Set(next)];
    level += 1;
  }

  // Anything left unvisited is part of a cycle -- place it one level past
  // everything already laid out rather than looping forever.
  const cycleLevel = level;
  for (const id of ids) {
    if (!visited.has(id)) {
      levels.set(id, cycleLevel);
      visited.add(id);
    }
  }

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

// --- Rendering -----------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

function renderGraph(graph) {
  const svg = document.getElementById("graph-svg");
  svg.innerHTML = "";

  const positions = computeLayout(graph.nodes, graph.edges);
  state.nodePositions = positions;

  const maxX = Math.max(0, ...Object.values(positions).map((p) => p.x)) + 160;
  const maxY = Math.max(0, ...Object.values(positions).map((p) => p.y)) + 60;
  svg.setAttribute("viewBox", `0 0 ${maxX} ${maxY}`);
  svg.setAttribute("height", Math.max(maxY, 400));

  const edgeLayer = document.createElementNS(SVG_NS, "g");
  const nodeLayer = document.createElementNS(SVG_NS, "g");

  for (const edge of graph.edges) {
    const from = positions[edge.source];
    const to = positions[edge.target];
    if (!from || !to) continue; // defensive: same reasoning as computeLayout
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", from.x);
    line.setAttribute("y1", from.y);
    line.setAttribute("x2", to.x);
    line.setAttribute("y2", to.y);
    line.setAttribute("class", "edge-line" + (edge.metadata?.origin === "inferred" ? " inferred" : ""));
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = `${edge.edge_type} (${edge.metadata?.origin ?? "unknown"})`;
    line.appendChild(title);
    edgeLayer.appendChild(line);
  }

  for (const node of graph.nodes) {
    const pos = positions[node.id];
    if (!pos) continue;
    const hasFinding = state.findingsByComponent.has(node.id);

    const group = document.createElementNS(SVG_NS, "g");
    group.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);
    group.style.cursor = "pointer";
    group.addEventListener("click", () => selectNode(node.id));

    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("r", "26");
    circle.setAttribute("class", "node-circle" + (hasFinding ? " has-security-finding" : ""));
    circle.dataset.nodeId = node.id;
    group.appendChild(circle);

    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("class", "node-label");
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("y", "42");
    label.textContent = truncate(node.name, 16);
    group.appendChild(label);

    const subLabel = document.createElementNS(SVG_NS, "text");
    subLabel.setAttribute("class", "node-sub-label");
    subLabel.setAttribute("text-anchor", "middle");
    subLabel.setAttribute("y", "54");
    subLabel.textContent = node.node_type;
    group.appendChild(subLabel);

    nodeLayer.appendChild(group);
  }

  svg.appendChild(edgeLayer);
  svg.appendChild(nodeLayer);

  const findingCount = [...state.findingsByComponent.values()].reduce((sum, list) => sum + list.length, 0);
  document.getElementById("graph-summary").textContent =
    `${graph.nodes.length} components · ${graph.edges.length} relationships` +
    (findingCount > 0 ? ` · ${findingCount} security finding${findingCount === 1 ? "" : "s"}` : "");
}

function truncate(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function markSelectedNode(nodeId) {
  document.querySelectorAll(".node-circle").forEach((el) => {
    el.classList.toggle("selected", el.dataset.nodeId === nodeId);
  });
}

// --- Node selection / detail panel ----------------------------------------------

async function selectNode(nodeId) {
  state.selectedNodeId = nodeId;
  markSelectedNode(nodeId);

  const node = state.graph.nodes.find((n) => n.id === nodeId);
  document.getElementById("detail-empty").hidden = true;
  document.getElementById("detail-content").hidden = false;
  document.getElementById("detail-name").textContent = node.name;
  document.getElementById("detail-meta").textContent = `${node.technology} · ${node.node_type}`;

  setTabLoading("explain");
  setTabLoading("impact");
  setTabLoading("dependencies");
  renderSecurityTab(nodeId); // synchronous -- findings were already fetched with the analysis

  explainNode(state.analysisId, nodeId)
    .then(renderExplainTab)
    .catch((err) => renderTabError("explain", err));

  impactOfNode(state.analysisId, nodeId)
    .then(renderImpactTab)
    .catch((err) => renderTabError("impact", err));

  dependenciesOfNode(state.analysisId, nodeId)
    .then(renderDependenciesTab)
    .catch((err) => renderTabError("dependencies", err));
}

function setTabLoading(tab) {
  document.getElementById(`${tab}-content`).textContent = "Loading…";
}

function renderTabError(tab, err) {
  document.getElementById(`${tab}-content`).textContent = `Could not load: ${err.message}`;
}

function renderExplainTab(result) {
  const el = document.getElementById("explain-content");
  el.innerHTML = "";
  const p = document.createElement("p");
  p.textContent = result.explanation;
  el.appendChild(p);
  const badge = document.createElement("span");
  badge.className = "confidence-badge";
  badge.textContent = `confidence: ${result.confidence} · ${result.generation_method}`;
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
  if (nodes.length === 0) {
    return wrapper;
  }
  const list = document.createElement("ul");
  list.className = "rel-list";
  for (const n of nodes) {
    const li = document.createElement("li");
    li.textContent = `${n.name} (${n.node_type})`;
    list.appendChild(li);
  }
  wrapper.appendChild(list);
  return wrapper;
}

function renderSecurityTab(nodeId) {
  const el = document.getElementById("security-content");
  el.innerHTML = "";
  const findings = state.findingsByComponent.get(nodeId) ?? [];
  if (findings.length === 0) {
    const p = document.createElement("p");
    p.textContent = "No security findings for this component.";
    el.appendChild(p);
    return;
  }
  for (const finding of findings) {
    const card = document.createElement("div");
    card.className = "finding";

    const title = document.createElement("div");
    title.className = "finding-title";
    title.textContent = finding.title;
    const badge = document.createElement("span");
    badge.className = `severity-badge severity-${finding.severity}`;
    badge.textContent = finding.severity;
    title.appendChild(badge);
    card.appendChild(title);

    const reason = document.createElement("div");
    reason.textContent = finding.reason;
    card.appendChild(reason);

    el.appendChild(card);
  }
}

// --- Tabs ------------------------------------------------------------------------

function setupTabs() {
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = button.dataset.tab;
      document.querySelectorAll(".tab-button").forEach((b) => b.classList.toggle("active", b === button));
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.dataset.tabPanel !== tab;
      });
    });
  });
}

// --- Analyze flow ------------------------------------------------------------------

function setStatus(message, isError = false) {
  const el = document.getElementById("status");
  el.textContent = message;
  el.classList.toggle("error", isError);
}

async function handleAnalyzeSubmit(event) {
  event.preventDefault();
  const repoUrl = document.getElementById("repo-url-input").value.trim();
  if (!repoUrl) return;

  const button = document.getElementById("analyze-button");
  button.disabled = true;
  setStatus("Cloning and analyzing repository…");
  document.getElementById("app").hidden = true;

  try {
    const analyzeResponse = await analyzeRepo(repoUrl);
    state.analysisId = analyzeResponse.analysis_id;
    state.graph = analyzeResponse.graph;
    state.selectedNodeId = null;

    setStatus("Loading security findings…");
    const securityResponse = await fetchSecurityFindings(state.analysisId);
    state.findingsByComponent = new Map();
    for (const finding of securityResponse.findings) {
      const list = state.findingsByComponent.get(finding.component_id) ?? [];
      list.push(finding);
      state.findingsByComponent.set(finding.component_id, list);
    }

    renderGraph(state.graph);
    document.getElementById("app").hidden = false;
    document.getElementById("detail-empty").hidden = false;
    document.getElementById("detail-content").hidden = true;
    setStatus(`Analyzed ${analyzeResponse.repository} — analysis_id ${state.analysisId}`);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    button.disabled = false;
  }
}

// --- Init --------------------------------------------------------------------------

function init() {
  document.getElementById("analyze-form").addEventListener("submit", handleAnalyzeSubmit);
  setupTabs();
}

if (typeof module !== "undefined" && module.exports) {
  // Allow computeLayout to be imported and tested under Node without a DOM.
  module.exports = { computeLayout };
} else {
  document.addEventListener("DOMContentLoaded", init);
}
