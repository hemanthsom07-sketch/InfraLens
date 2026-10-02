import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js";
import { OrbitControls } from "https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/OrbitControls.js";

const container = document.getElementById("graph-3d");
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x050b14);
scene.fog = new THREE.FogExp2(0x050b14, 0.0009);
const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 6000);
camera.position.set(0, 95, 300);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.setSize(1, 1);
container.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.075;
controls.minDistance = 28;
controls.maxDistance = 1800;
controls.maxPolarAngle = Math.PI * 0.86;
controls.target.set(0, 0, 0);

scene.add(new THREE.HemisphereLight(0xb9d8ff, 0x07111f, 2.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.7);
keyLight.position.set(180, 260, 220);
scene.add(keyLight);
const rimLight = new THREE.PointLight(0x4cc9f0, 75, 900, 2);
rimLight.position.set(-240, 120, 160);
scene.add(rimLight);

const world = new THREE.Group();
scene.add(world);
const environment = new THREE.Group();
scene.add(environment);
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const nodeMeshes = new Map();
const nodeObjects = new Map();
const nodeMaterials = new Map();
const nodeRings = new Map();
const edgeObjects = [];
let selectedId = null;
let selectCallback = null;
let hoverCallback = null;
let currentGraph = null;
let viewMode = "topology";
let labelsVisible = true;
let currentFindings = new Map();
let animationFrame = 0;
let currentHoverId = null;

const TYPE_COLORS = {
  service: 0x4cc9f0, container: 0x7c83fd, database: 0xf59e0b, network: 0x34d399,
  volume: 0xa78bfa, ingress: 0xf472b6, namespace: 0x94a3b8, configmap: 0x22d3ee,
  secret: 0xef4444, serviceaccount: 0xfacc15, persistentvolumeclaim: 0xa78bfa,
  kubernetes_resource: 0x60a5fa, terraform_resource: 0x8b5cf6,
};

function colorForNode(node, hasFinding) {
  if (hasFinding) return 0xf43f5e;
  return TYPE_COLORS[String(node.node_type || "").toLowerCase()] ?? 0x64748b;
}

function disposeObject(object) {
  object.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      materials.forEach((material) => { if (material.map) material.map.dispose(); material.dispose(); });
    }
  });
}

function disposeWorld() {
  while (world.children.length) disposeObject(world.children.pop());
  nodeMeshes.clear(); nodeObjects.clear(); nodeMaterials.clear(); nodeRings.clear(); edgeObjects.length = 0;
}

function createEnvironment() {
  while (environment.children.length) disposeObject(environment.children.pop());
  const grid = new THREE.GridHelper(1100, 44, 0x20344b, 0x102033);
  grid.position.y = -58; grid.material.transparent = true; grid.material.opacity = 0.42; environment.add(grid);
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(270, 271.5, 96),
    new THREE.MeshBasicMaterial({ color: 0x18334c, transparent: true, opacity: 0.42, side: THREE.DoubleSide }),
  );
  ring.rotation.x = -Math.PI / 2; ring.position.y = -56; environment.add(ring);
  const starPositions = new Float32Array(900 * 3);
  for (let i = 0; i < 900; i += 1) {
    const radius = 420 + Math.random() * 950;
    const angle = Math.random() * Math.PI * 2;
    const height = -10 + Math.random() * 650;
    starPositions[i * 3] = Math.cos(angle) * radius;
    starPositions[i * 3 + 1] = height;
    starPositions[i * 3 + 2] = Math.sin(angle) * radius;
  }
  const starsGeometry = new THREE.BufferGeometry();
  starsGeometry.setAttribute("position", new THREE.BufferAttribute(starPositions, 3));
  environment.add(new THREE.Points(starsGeometry, new THREE.PointsMaterial({ color: 0x6c8aa8, size: 1.7, transparent: true, opacity: 0.42, sizeAttenuation: true })));
}
createEnvironment();

function compute3DPositions(nodes, edges) {
  const ids = nodes.map((n) => n.id);
  const outgoing = new Map(ids.map((id) => [id, []]));
  const indegree = new Map(ids.map((id) => [id, 0]));
  for (const edge of edges) {
    if (!outgoing.has(edge.source) || !indegree.has(edge.target)) continue;
    outgoing.get(edge.source).push(edge.target); indegree.set(edge.target, indegree.get(edge.target) + 1);
  }
  const remaining = new Map(indegree); let frontier = ids.filter((id) => remaining.get(id) === 0);
  const levels = new Map(); const visited = new Set(); let level = 0;
  while (frontier.length) {
    for (const id of frontier) { levels.set(id, level); visited.add(id); }
    const next = [];
    for (const id of frontier) for (const target of outgoing.get(id) || []) {
      if (visited.has(target)) continue;
      remaining.set(target, remaining.get(target) - 1);
      if (remaining.get(target) === 0) next.push(target);
    }
    frontier = [...new Set(next)]; level += 1;
  }
  for (const id of ids) if (!visited.has(id)) levels.set(id, level);
  const buckets = new Map();
  for (const id of ids) { const current = levels.get(id); if (!buckets.has(current)) buckets.set(current, []); buckets.get(current).push(id); }
  const positions = new Map(); const levelCount = Math.max(1, buckets.size);
  for (const [current, bucket] of buckets) {
    const spread = Math.max(1, bucket.length - 1) * 82;
    bucket.forEach((id, index) => {
      const y = index * 82 - spread / 2;
      const z = Math.sin(index * 1.8 + current) * 34 + ((index % 3) - 1) * 18;
      const x = current * 175 - ((levelCount - 1) * 175) / 2;
      positions.set(id, new THREE.Vector3(x, y, z));
    });
  }
  return positions;
}

function createLabel(text, color, type) {
  const canvas = document.createElement("canvas"); canvas.width = 720; canvas.height = 170;
  const ctx = canvas.getContext("2d"); ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.font = "700 30px system-ui, sans-serif"; ctx.fillStyle = color; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text.length > 28 ? `${text.slice(0, 27)}...` : text, 360, 58);
  ctx.font = "600 19px system-ui, sans-serif"; ctx.fillStyle = "#728aa4";
  ctx.fillText(String(type || "component").replaceAll("_", " "), 360, 104);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }));
  sprite.scale.set(92, 22, 1); sprite.position.y = -29; return sprite;
}

function geometryForType(type) {
  switch (String(type || "").toLowerCase()) {
    case "service": return new THREE.BoxGeometry(18, 18, 18, 2, 2, 2);
    case "database": return new THREE.CylinderGeometry(13, 13, 18, 24);
    case "secret": return new THREE.OctahedronGeometry(15, 1);
    case "ingress": return new THREE.ConeGeometry(16, 23, 6);
    case "container": return new THREE.DodecahedronGeometry(14, 1);
    case "network": return new THREE.TorusGeometry(12, 4, 10, 24);
    case "volume":
    case "persistentvolumeclaim": return new THREE.CylinderGeometry(11, 15, 19, 8);
    case "namespace": return new THREE.TorusKnotGeometry(10, 3, 48, 8);
    default: return new THREE.IcosahedronGeometry(13, 1);
  }
}

function addNode(node, position, hasFinding) {
  const color = colorForNode(node, hasFinding); const group = new THREE.Group(); group.position.copy(position); group.userData.nodeId = node.id;
  const mesh = new THREE.Mesh(geometryForType(node.node_type), new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: hasFinding ? 0.45 : 0.11, roughness: 0.34, metalness: 0.48 }));
  mesh.userData.nodeId = node.id; group.add(mesh);
  group.add(new THREE.Mesh(new THREE.SphereGeometry(hasFinding ? 22 : 19, 20, 20), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: hasFinding ? 0.08 : 0.035, depthWrite: false, side: THREE.BackSide })));
  const ring = new THREE.Mesh(new THREE.TorusGeometry(hasFinding ? 20 : 17, 0.8, 8, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: hasFinding ? 0.8 : 0.25 }));
  ring.rotation.x = Math.PI / 2; ring.position.y = -18; group.add(ring);
  const label = createLabel(node.name, hasFinding ? "#fecdd3" : "#dbeafe", node.node_type);
  label.visible = labelsVisible; group.add(label); world.add(group);
  nodeMeshes.set(node.id, mesh); nodeObjects.set(node.id, group); nodeMaterials.set(node.id, mesh.material); nodeRings.set(node.id, ring);
}

function addArrow(from, to, color, opacity) {
  return addCurvedEdge(from, to, color, opacity, false);
}

function addCurvedEdge(from, to, color, opacity, inferred) {
  const midpoint = new THREE.Vector3().addVectors(from, to).multiplyScalar(0.5);
  const perpendicular = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3().subVectors(to, from)).normalize();
  if (!Number.isFinite(perpendicular.x)) perpendicular.set(0, 0, 1);
  midpoint.addScaledVector(perpendicular, Math.min(38, from.distanceTo(to) * 0.16));
  const curve = new THREE.QuadraticBezierCurve3(from, midpoint, to);
  const lineMaterial = new THREE.LineBasicMaterial({ color, transparent: true, opacity });
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(18)), lineMaterial);
  const direction = new THREE.Vector3().subVectors(to, midpoint).normalize(); const arrowPosition = curve.getPoint(0.86);
  const arrow = new THREE.Mesh(new THREE.ConeGeometry(4.8, 13, 10), new THREE.MeshBasicMaterial({ color, transparent: true, opacity }));
  arrow.position.copy(arrowPosition); arrow.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
  const group = new THREE.Group(); group.add(line, arrow); world.add(group);
  return { group, lineMaterial, arrowMaterial: arrow.material, source: null, target: null, inferred };
}

function shortestPath(source, targets, edges) {
  const targetSet = new Set(targets); if (targetSet.has(source)) return [source];
  const outgoing = new Map(); for (const edge of edges) { if (!outgoing.has(edge.source)) outgoing.set(edge.source, []); outgoing.get(edge.source).push(edge.target); }
  const queue = [source]; const previous = new Map([[source, null]]);
  while (queue.length) { const current = queue.shift(); for (const next of outgoing.get(current) || []) {
    if (previous.has(next)) continue; previous.set(next, current);
    if (targetSet.has(next)) { const path = []; let cursor = next; while (cursor !== null) { path.unshift(cursor); cursor = previous.get(cursor); } return path; }
    queue.push(next);
  }}
  return [];
}

function securityPathEdgeKeys(graph, findingsByComponent) {
  const secrets = graph.nodes.filter((node) => String(node.node_type || "").toLowerCase() === "secret").map((node) => node.id); const keys = new Set();
  for (const [componentId, findings] of findingsByComponent.entries()) {
    if (!findings.some((finding) => finding.rule_id === "EXPOSED_PATH_TO_SECRET")) continue;
    const path = shortestPath(componentId, secrets, graph.edges);
    for (let i = 0; i < path.length - 1; i += 1) keys.add(`${path[i]}->${path[i + 1]}`);
  }
  return keys;
}

function applySelectionVisuals() {
  if (!currentGraph) return;
  const connected = new Set();
  if (selectedId) {
    connected.add(selectedId);
    for (const edge of currentGraph.edges) {
      if (edge.source === selectedId) connected.add(edge.target);
      if (edge.target === selectedId) connected.add(edge.source);
    }
  }
  const pathEdges = securityPathEdgeKeys(currentGraph, currentFindings);
  for (const [id, mesh] of nodeMeshes.entries()) {
    const material = nodeMaterials.get(id);
    const isSecurity = currentFindings.has(id);
    const dependencyActive = !selectedId || connected.has(id);
    const securityActive = isSecurity || [...currentGraph.edges].some((edge) => pathEdges.has(`${edge.source}->${edge.target}`) && (edge.source === id || edge.target === id));
    const active = viewMode === "security" ? securityActive : viewMode === "dependencies" ? dependencyActive : true;
    material.opacity = active ? 1 : 0.12;
    material.transparent = !active;
    material.emissiveIntensity = id === selectedId ? 0.78 : isSecurity ? 0.46 : 0.11;
    mesh.scale.setScalar(id === selectedId ? 1.24 : isSecurity ? 1.08 : 1);
    const ring = nodeRings.get(id);
    if (ring) ring.material.opacity = id === selectedId ? 1 : isSecurity ? 0.82 : active ? 0.25 : 0.05;
    const object = nodeObjects.get(id);
    if (object) object.userData.active = active;
  }
  for (const item of edgeObjects) {
    const key = `${item.source}->${item.target}`;
    const touchesSelection = !selectedId || item.source === selectedId || item.target === selectedId;
    const securityPath = pathEdges.has(key);
    const securityVisible = securityPath || currentFindings.has(item.source) || currentFindings.has(item.target);
    const dependencyVisible = touchesSelection;
    const modeVisible = viewMode === "security" ? securityVisible : viewMode === "dependencies" ? dependencyVisible : true;
    const color = securityPath ? 0xf43f5e : item.inferred ? 0xf59e0b : 0x52708e;
    item.lineMaterial.color.setHex(color); item.arrowMaterial.color.setHex(color);
    const opacity = !modeVisible ? 0.03 : securityPath ? 1 : touchesSelection ? (item.inferred ? 0.92 : 0.72) : 0.08;
    item.lineMaterial.opacity = opacity; item.arrowMaterial.opacity = opacity;
  }
}

function setLabelsVisible(visible) {
  labelsVisible = Boolean(visible);
  for (const object of nodeObjects.values()) {
    object.children.forEach((child) => { if (child.isSprite) child.visible = labelsVisible; });
  }
}

function setViewMode(mode) {
  viewMode = ["topology", "dependencies", "security"].includes(mode) ? mode : "topology";
  applySelectionVisuals();
}

function fitView() {
  const nodes = [...nodeObjects.values()]; if (!nodes.length) return; const box = new THREE.Box3(); nodes.forEach((node) => box.expandByObject(node));
  const center = box.getCenter(new THREE.Vector3()); const size = box.getSize(new THREE.Vector3()); const radius = Math.max(size.x, size.y, size.z, 100);
  const distance = Math.min(1350, Math.max(170, radius * 1.75)); const direction = new THREE.Vector3(0.62, 0.42, 1).normalize();
  camera.position.copy(center).addScaledVector(direction, distance); controls.target.copy(center); controls.update();
}

function resetView() { camera.position.set(0, 95, 300); controls.target.set(0, 0, 0); controls.update(); selectedId = null; applySelectionVisuals(); }

function render(graph, findingsByComponent, onSelect, onHover) {
  disposeWorld(); selectCallback = onSelect; hoverCallback = onHover; currentHoverId = null; selectedId = null; currentGraph = graph; currentFindings = findingsByComponent;
  if (!graph || !graph.nodes.length) { resetView(); return; }
  const positions = compute3DPositions(graph.nodes, graph.edges);
  graph.nodes.forEach((node) => addNode(node, positions.get(node.id), findingsByComponent.has(node.id)));
  graph.edges.forEach((edge) => { const from = positions.get(edge.source); const to = positions.get(edge.target); if (!from || !to) return;
    const inferred = edge.metadata?.origin === "inferred"; const item = addCurvedEdge(from, to, inferred ? 0xf59e0b : 0x52708e, inferred ? 0.82 : 0.58, inferred); item.source = edge.source; item.target = edge.target; edgeObjects.push(item);
  });
  fitView(); applySelectionVisuals();
}

function select(nodeId) { selectedId = nodeId; applySelectionVisuals(); if (nodeObjects.has(nodeId)) controls.target.copy(nodeObjects.get(nodeId).position); }
function clearSelection() { selectedId = null; applySelectionVisuals(); }
function focusNode(nodeId) { if (!nodeObjects.has(nodeId)) return; selectedId = nodeId; const position = nodeObjects.get(nodeId).position; controls.target.copy(position); camera.position.copy(position).add(new THREE.Vector3(95, 60, 120)); controls.update(); applySelectionVisuals(); }
function resize() { const width = container.clientWidth || 1; const height = container.clientHeight || 1; camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.setSize(width, height, false); }
function hitNode(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects([...nodeMeshes.values()], false);
  return hits.length ? hits[0].object.userData.nodeId : null;
}
renderer.domElement.addEventListener("pointermove", (event) => {
  const nodeId = hitNode(event);
  if (nodeId !== currentHoverId) { currentHoverId = nodeId; if (hoverCallback) hoverCallback(nodeId); renderer.domElement.style.cursor = nodeId ? "pointer" : "default"; }
});
renderer.domElement.addEventListener("pointerleave", () => { currentHoverId = null; if (hoverCallback) hoverCallback(null); renderer.domElement.style.cursor = "default"; });
renderer.domElement.addEventListener("pointerdown", (event) => {
  const nodeId = hitNode(event); if (!nodeId) return;
  select(nodeId); if (selectCallback) selectCallback(nodeId);
});
renderer.domElement.addEventListener("dblclick", (event) => { const nodeId = hitNode(event); if (nodeId) focusNode(nodeId); });
window.addEventListener("resize", resize); resize();

function animate() {
  animationFrame = requestAnimationFrame(animate); controls.update(); const time = performance.now() * 0.001;
  for (const [id, ring] of nodeRings.entries()) { const finding = currentFindings.has(id); ring.rotation.z = time * (finding ? 0.75 : 0.22); if (finding) ring.scale.setScalar(1 + Math.sin(time * 3.2) * 0.05); }
  environment.rotation.y = Math.sin(time * 0.025) * 0.015; renderer.render(scene, camera);
}
animate();
window.addEventListener("beforeunload", () => cancelAnimationFrame(animationFrame));
window.infraLens3D = { render, select, clearSelection, focusNode, fitView, resetView, setViewMode, setLabelsVisible };
