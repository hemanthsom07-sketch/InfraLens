# InfraLens — Phase 4 Architecture: The Graph Engine

**Status:** Design only — no implementation code, per the phase brief.
**Builds on:** Phase 1 (scanning), Phase 2 (tech detection), Phase 3 (Infrastructure Knowledge Model + parsers).
**Goal:** Turn the IKM's flat `components` / `relationships` lists into a queryable graph that becomes the one shared foundation for every future feature (AI explanations, security analysis, cloud insights, frontend visualization).

---

## 0. Framing: what the Graph Engine is and isn't

The IKM (Phase 3) already answers *"what infrastructure exists and how is it declared to relate?"* The Graph Engine answers a different, harder question: *"given all of that, what can I ask about the system as a whole?"* — what depends on what, transitively; what's reachable from what; what would break if X changed; what's structurally isolated; is there a cycle.

That reframing drives every decision below. The Graph Engine is not a data-format converter that happens to rename `components` to `nodes`. It's a **query and analysis layer** — the IKM is its input, not its identity.

One boundary worth stating up front, since it shapes several decisions later: like every layer before it, the Graph Engine is designed to build fresh **per request**, from that request's IKM, with nothing persisted server-side between requests. That's a deliberate continuation of the no-database, ephemeral-clone philosophy from Phase 1, not an oversight — flagged explicitly in §12 and revisited in §13.

---

## 1. Overall Architecture — Where the Graph Engine Fits

```
┌──────────────────────┐
│  GitHub Repository     │
└───────────┬────────────┘
            │ git clone --depth 1
            ▼
┌──────────────────────┐
│  Repository Scanner     │   scanner_service.py  (Phase 1)
│  tree · file count      │
│  · language detection   │
└───────────┬────────────┘
            │ ScanResult.file_paths
            ▼
┌──────────────────────────────┐
│  Technology Detection           │   framework_service.py
│  frameworks · infra tooling      │   infrastructure_service.py  (Phase 2)
│  (flat list of names)            │
└───────────┬──────────────────────┘
            │ file_paths, filtered by is_dockerfile() / is_compose_file() / ...
            ▼
┌──────────────────────────────┐
│  Infrastructure Parsers          │   app/parsers/*  (Phase 3)
│  Docker · Compose · Terraform    │   (each: InfrastructureParser subclass)
│  · Kubernetes                    │
└───────────┬──────────────────────┘
            │ Component[] + Relationship[]  (per file, merged by ikm_service.py)
            ▼
┌──────────────────────────────┐
│  Infrastructure Knowledge Model  │   app/models/ikm.py  (Phase 3)
│  components: list[Component]     │
│  relationships: list[Relationship]│
└───────────┬──────────────────────┘
            │ InfrastructureModel  (flat, declarative, no traversal capability)
            ▼
╔═══════════════════════════════════╗
║        ★  GRAPH ENGINE  ★           ║   app/graph/*   ◄── PHASE 4 (this doc)
║                                      ║
║  GraphBuilder   → maps IKM to a      ║
║                    working graph,    ║
║                    refines types,    ║
║                    infers extra      ║
║                    relationships     ║
║                                      ║
║  GraphEngine    → the public facade: ║
║                    queries, traversal║
║                    algorithms         ║
╚═══════════════════╦═════════════════╝
                    │ GraphEngine instance (queryable, in-memory)
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                          FUTURE SYSTEMS                             │
│                                                                       │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌──────────┐│
│  │ AI Explanation │ │   Security     │ │ Cloud Insights │ │ Frontend  ││
│  │ Engine          │ │   Analysis     │ │                │ │ Visual.   ││
│  │ (Phase 5)        │ │  (Phase 6)     │ │  (Phase 7)      │ │ (Phase 8) ││
│  └───────────────┘ └───────────────┘ └───────────────┘ └──────────┘│
│         all consume GraphEngine's public API only — none of them      │
│         touch the IKM, the parsers, or the raw graph internals         │
└─────────────────────────────────────────────────────────────────┘
```

**The key architectural property this establishes:** every layer only talks to the layer directly below it, and only through that layer's public surface. The Graph Engine is a **strict boundary** — Phase 5–8 never reach past it into the IKM or the parsers, and the Graph Engine never reaches back into knowing *which* parser produced a component. This is the same discipline Phase 3 already applied ("parsers don't know about graphs"); Phase 4 just moves the line one layer up ("the graph doesn't know about security, cost, or AI").

---

## 2. Graph Data Model

### 2.1 Design principle: two representations, not one

A graph needs to serve two different purposes that pull in different directions:

- **A wire format** — simple, flat, JSON-serializable, stable for API consumers (especially the future frontend).
- **A working representation** — indexed for fast traversal (O(1) "give me the neighbors of X"), because every algorithm in §4 depends on that.

Modeling both needs with *one* structure means picking a bad compromise for at least one of them (a flat Pydantic list of edges is bad for traversal; a library graph object is not directly JSON-serializable). So the design deliberately keeps them separate:

| | Wire format | Working representation |
|---|---|---|
| Where | `app/models/graph.py` | `app/graph/` (internal) |
| Shape | `Node[]` + `Edge[]`, flat, Pydantic | Indexed adjacency structure |
| Used by | API responses, frontend | `GraphEngine`'s algorithms |
| Built from | `GraphEngine.to_model()` | `GraphBuilder.build()` |

This mirrors the same reasoning Phase 1 already applied when `scan_repository()` was changed to return a `ScanResult` dataclass instead of a bare tuple — internal working data and external contract data don't have to be, and often shouldn't be, the same shape.

### 2.2 Node

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | **Reused directly from `Component.id`.** Phase 3's IDs (`docker:backend/Dockerfile`, `kubernetes:k8s/deploy.yaml:Deployment:api`, ...) are already globally unique across the whole IKM by construction — the Graph Engine inherits that guarantee for free instead of inventing a second ID scheme. |
| `name` | `str` | From `Component.name`. |
| `node_type` | `str` | Starts from `Component.type`, **optionally refined** (see §3.3) — e.g. a `kubernetes_resource` with `metadata.kind == "Ingress"` becomes node_type `"ingress"`. Open `str`, not a closed enum — same reasoning as `ComponentType` in the IKM: new technologies must be able to introduce new types without a model change. |
| `technology` | `str` | Passed through from `Component.technology` unchanged (`docker`, `docker-compose`, `terraform`, `kubernetes`, ...). |
| `metadata` | `dict[str, Any]` | Passed through from `Component.metadata` unchanged — the Graph Engine doesn't reinterpret or strip it. |

### 2.3 Edge

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | New at this layer (Relationship has no id). Derived deterministically, e.g. `f"{source}--{relationship_type}-->{target}"`. Useful as a stable frontend render key and as something a future phase (e.g. Security) can reference directly ("flag *this* edge"). |
| `source` | `str` | Node id. |
| `target` | `str` | Node id. |
| `edge_type` | `str` | From `Relationship.relationship_type` (`depends_on`, `connects_to`, `uses`, `contains`, `mounts`, ...) for directly-parsed edges, or a new value for inferred ones (see §3.4). |
| `metadata` | `dict[str, Any]` | **Yes, edges carry metadata** — same open-dict pattern as nodes. Two things always go here: <br>• `"origin": "parsed" \| "inferred"` <br>• for inferred edges only, `"confidence": "high" \| "medium" \| "heuristic"` and `"basis"` (a short string explaining *why* it was inferred, e.g. `"label selector match"`). |

**Directed, not undirected.** Every relationship type that exists today — `depends_on`, `uses`, `contains`, `mounts`, and even `connects_to` in practice — has a clear source and target. A `depends_on` edge from A to B is not interchangeable with one from B to A; collapsing that distinction would break cycle detection, ancestor/descendant queries, and impact analysis (§4) outright. Every algorithm below assumes a directed graph.

### 2.4 GraphModel (the wire format)

```
GraphModel
├── nodes: list[Node]
├── edges: list[Edge]
└── metadata: dict[str, Any]      # e.g. { "node_count": .., "edge_count": .., "has_cycles": bool }
```

A trailing `metadata` dict at the *graph* level (not just node/edge level) is worth calling out specifically: it's the natural place for a small set of **precomputed, cheap-to-answer facts** (node/edge counts, whether cycles exist) that the frontend or an LLM prompt can use immediately without an extra round trip — see §5's note on what's precomputed vs. queried on demand.

---

## 3. Graph Building Strategy

### 3.1 Baseline mapping

Every `Component` becomes exactly one `Node`; every `Relationship` becomes exactly one `Edge`. This baseline mapping is mechanical and lossless — nothing from the IKM is dropped. Everything below this line is the Graph Engine adding value *on top of* that baseline, not replacing it.

### 3.2 Should every Component become a Node? Yes — no filtering at build time

It's tempting to have the builder skip components that look "unimportant" (e.g. a `ConfigMap` with no relationships). Resist this: filtering is a **view concern**, not a **construction concern** — a future frontend or query might legitimately want to filter *differently* per use case (e.g. Security Analysis wants everything, a first-glance frontend view wants to collapse config/secrets). The graph should always be built complete; filtering happens in `GraphEngine.find_nodes(...)` / subgraph extraction, downstream of construction, where it can be parameterized instead of baked in.

### 3.3 Type refinement

The IKM's `Component.type` is deliberately coarse in one place: every Kubernetes object — `Deployment`, `Service`, `ConfigMap`, `Secret`, `Ingress`, `StatefulSet` — shares the single type `kubernetes_resource`, with the specific kind sitting in `metadata["kind"]`. That's the right call *for the IKM* (Phase 3's job was extraction, not classification), but it's too coarse for a graph a frontend wants to color/filter/lay out by type, and too coarse for the node-type examples the phase brief itself lists (`Ingress` as its own category, not lumped under a generic K8s bucket).

So the builder applies a **refinement step**: a lookup table, not a hardcoded chain of `if kind == "..."`, maps `(technology, metadata.kind)` → a more specific `node_type`:

```
("kubernetes", "Deployment")  → "container"
("kubernetes", "StatefulSet") → "container"
("kubernetes", "Service")     → "service"
("kubernetes", "Ingress")     → "ingress"
("kubernetes", "ConfigMap")   → "config"
("kubernetes", "Secret")      → "secret"
```

Being a lookup table rather than branching logic matters for §6 (Extensibility): adding refinement for a new technology later is a data change (extend the table), never a code change to the builder.

### 3.4 Inferred relationships — where the Graph Engine earns its name

This is the most consequential design question in the whole phase (`"Should the Graph Engine infer additional relationships?"`), and the answer is **yes, but scoped to three specific, well-justified inferences** — not open-ended heuristic guessing:

**a) Kubernetes Service → Deployment/StatefulSet, via label selector matching.**
Phase 3's `kubernetes_parser.py` docstring explicitly deferred this: *"linking a Service to the Deployment it fronts requires matching the Service's label selector against Pod template labels... a natural fit for the future Graph Engine phase."* This is Phase 4 making good on that. Crucially, this one isn't a heuristic at all — Kubernetes' *own* semantics define exactly how a Service selects Pods (`spec.selector` vs. the pod template's `metadata.labels`), so when both are present in the graph, the match is as authoritative as anything directly parsed. `origin: "inferred"`, `confidence: "high"`.

**b) Compose service → the Dockerfile it builds**, via `build_context` path matching. If a Compose service's `metadata.build_context` (e.g. `"./backend"`) resolves to a directory containing a `Dockerfile` component already in the graph, add a `uses` edge from the service to that Dockerfile. This is a straightforward, low-risk path correlation. `confidence: "high"`.

**c) Cross-technology image correlation.** If a Kubernetes container's image string (or a Compose service's `image`) matches another component's declared image (accounting for a missing/`:latest` tag), add a `uses`/`connects_to` edge between them. This is the genuinely heuristic one — image strings are just strings, with no semantic guarantee two matching ones are "the same" image in a meaningful sense — so it's explicitly tagged `confidence: "heuristic"`, and every consumer (especially Security Analysis) is expected to treat it as a hint, not a fact.

**What the Graph Engine deliberately does *not* try to infer:** anything requiring semantics outside what's already in the components' metadata (e.g. guessing network topology from `depends_on` chains, or inferring cloud IAM relationships). If it's not derivable from data the parsers already extracted, it's out of scope for inference — the alternative is silently fabricating structure, which is worse than not having it.

### 3.5 Confidence and provenance as first-class metadata

Every edge — parsed or inferred — carries `origin` and (for inferred ones) `confidence` and `basis` in its metadata, as defined in §2.3. This is a small addition with an outsized payoff for §8: the AI Explanation Engine can hedge language appropriately ("this service likely connects to..." vs. "this service depends on..."), and Security Analysis can choose to exclude heuristic edges from anything security-critical entirely.

---

## 4. Algorithms

| Algorithm | Why InfraLens needs it |
|---|---|
| **Dependency traversal** (BFS/DFS, following `depends_on`/`uses`/`contains`/`mounts`) | The foundation everything else is built on: "what does X need to run" (descendants) and "what needs X" (ancestors). Directly powers ordered, human-readable explanations in Phase 5. |
| **Cycle detection** | Not just a graph curiosity — a `depends_on` cycle is a **real infrastructure bug** (Compose won't start it, Terraform won't apply it). This is a correctness check with immediate, actionable value, independent of any later "security" framing. |
| **Shortest path** | "How does my frontend eventually reach the database, and through how many hops" — the connective narrative Phase 5 needs, and the exact mechanism behind Phase 6's attack-surface question: *shortest path from an externally-reachable node (Ingress, exposed port) to a sensitive one (Database, Secret)*. |
| **Graph search / filtered lookup** | Less an "algorithm" than a query primitive (`find_nodes(type=, technology=, name_pattern=)`), but essential — no consumer should ever have to manually walk the whole node list to answer "which components use Postgres." |
| **Connected components** | Surfaces structurally isolated nodes — infrastructure declared but never wired to anything else. That's either dead/forgotten config (relevant to Phase 7's cost-waste angle) or a parsing/authoring gap worth surfacing on its own. |
| **Impact analysis** | The product's signature capability: *"what breaks if I change or remove this"* = the full set of ancestors of a node in the `depends_on` graph, computed via dependency traversal and returned as a structured report (direct vs. transitive impact, grouped by type) rather than a flat id list — see §5. |

**Two additions worth including even though not explicitly requested**, because they follow naturally from the above and cost little extra design surface:

- **Topological sort** — a natural pairing with cycle detection (only well-defined on a DAG; if cycle detection finds one, topological sort simply reports that it can't run). Gives a concrete "valid startup/build order" — genuinely useful output, not just a theoretical nicety.
- **Centrality** (degree centrality to start) — identifies the most-connected node in the graph, i.e. the single point of failure everything else quietly depends on. Flagged as a *candidate for Phase 4 or shortly after*, not committed to immediately — it's the first thing I'd reach for once the core six are solid.

**Should InfraLens hand-roll these, or use a library?** This is significant enough to warrant its own discussion — see §12 (Design Decisions) and §13 (Trade-offs). Short version: recommend NetworkX, fully encapsulated behind `GraphEngine` so the choice is swappable later without touching any consumer.

---

## 5. Public API Design (internal)

"Public" here means the **internal, programmatic API** other Python modules import and call — Phase 4 doesn't mandate new HTTP endpoints (see the note at the end of this section for that separate, smaller question).

### 5.1 Service boundary — mirrors the existing pattern exactly

Today, `api/v1/analyze.py` never imports from `app/parsers/` directly — it calls `ikm_service.build_infrastructure_model()`, which is the one function that knows how to reach into the parsers package. The Graph Engine follows the identical shape:

```
app/services/graph_service.py         (NEW, thin — the only file api/ ever imports from)
    build_graph(model: InfrastructureModel) -> GraphEngine
        1. GraphBuilder().build(model)   # -> internal Graph
        2. GraphEngine(graph)             # wraps it, ready to query
        return the GraphEngine
```

`api/v1/analyze.py` (or whatever calls this) only ever sees `graph_service.build_graph()` and the `GraphEngine` object it returns — never `GraphBuilder`, never the internal `Graph` representation, never `app/graph/` internals directly. Same discipline, one layer up.

### 5.2 Core classes

```
GraphBuilder
└── build(model: InfrastructureModel) -> Graph
    (construction only — mapping, type refinement, inference, all in one pass)

Graph                                    (internal working representation)
├── add_node(node: Node) -> None          # idempotent — see §7
├── add_edge(edge: Edge) -> None
├── get_node(node_id: str) -> Node | None
├── neighbors(node_id: str, direction: "in" | "out" | "both") -> list[Node]
└── (internally: adjacency index, and/or wraps a networkx.DiGraph — an
     implementation detail GraphEngine callers never see)

GraphEngine                               (the public facade — what everything above imports)
├── @classmethod from_infrastructure_model(model) -> GraphEngine    # convenience: builder + engine in one call
├── get_node(node_id) -> Node | None
├── find_nodes(node_type=None, technology=None, name_pattern=None) -> list[Node]
├── get_dependencies(node_id) -> list[Node]      # descendants
├── get_dependents(node_id) -> list[Node]         # ancestors
├── detect_cycles() -> list[list[Node]]
├── shortest_path(source_id, target_id) -> list[Node] | None
├── connected_components() -> list[list[Node]]
├── topological_order() -> list[Node] | None       # None if cyclic
├── impact_analysis(node_id) -> ImpactReport
└── to_model() -> GraphModel                        # -> the wire format, §2.4

ImpactReport                              (structured result, not a flat list)
├── target: Node
├── direct_dependents: list[Node]          # 1 hop
├── transitive_dependents: list[Node]      # 2+ hops
├── total_impact_count: int
└── impact_by_type: dict[str, int]          # e.g. {"service": 3, "database": 1}
```

`GraphEngine` is a facade over focused internal modules (`app/graph/algorithms/traversal.py`, `cycles.py`, `paths.py`, `components.py` — see §9) the same way `ikm_service.build_infrastructure_model()` is a thin dispatcher over four independent parser classes. One cohesive object for consumers to hold and mock in tests; several small, independently-testable pieces behind it.

### 5.3 What's precomputed vs. queried on demand

Cheap, always-useful facts (`has_cycles`, node/edge counts) are computed once during `build()` and cached in `GraphModel.metadata` — no consumer should have to ask "are there cycles?" via a full traversal just to decide whether to show a warning icon. Parametrized queries (`shortest_path` between two *specific* nodes a user picked in a UI, `impact_analysis` for one node) stay lazy/on-demand — there's no way to precompute an answer for every possible pair without wasted work.

### 5.4 A smaller, separate question: does this need new HTTP endpoints?

Not something Phase 4 needs to settle (the brief asks for the *internal* API), but worth flagging since it affects §8: the simplest option is to add `graph: GraphModel` as one more field on the existing `/analyze` response, exactly how `infrastructure_model` was added in Phase 3 — no new routes, no session/state. That covers the frontend's initial render. Genuinely *parametrized* queries (a user clicking two nodes and asking for the path between them) can then run **client-side**, against the graph the frontend already has in memory, using any JS graph library — avoiding a backend round trip and any need for server-side session state entirely. I'd default to this and only introduce a server-side cache (analysis-id → graph, with a TTL) if a concrete future need shows up — e.g. Phase 5 wanting to run many different queries server-side without re-sending the whole graph per call.

---

## 6. Extensibility

The core claim: **adding a new infrastructure technology should never require touching the Graph Engine.** This isn't aspirational — it falls directly out of decisions already made in Phase 3 and reinforced in §3.3 above. A new technology means a new `InfrastructureParser` subclass producing ordinary `Component`/`Relationship` objects; the Graph Engine only ever consumes the IKM's generic shape, never anything technology-specific. The one thing that *might* need a one-line addition is the type-refinement lookup table (§3.3) — and that's a data change, not a code change.

Technology-by-technology, since "just write a parser" undersells how different these actually are:

- **CloudFormation** — easiest of the five. Declarative JSON/YAML, structurally close to Terraform (`Resources:` section, each with a `Type` and logical name). Follows `terraform_parser.py`'s exact pattern.
- **Nomad** — also straightforward. HCL, same format family as Terraform; job/group/task blocks extract similarly to `resource` blocks.
- **Helm** — genuinely different problem, not just a new parser. Chart templates contain unrendered Go-template syntax (`{{ .Values.image.repository }}`), so a naive line-by-line parser would see template placeholders, not real values. The real solution: shell out to `helm template` (mirroring how `git_service.py` already shells out to `git`) to get *rendered* output — which is just plain Kubernetes YAML — and feed that straight through the **existing** `KubernetesParser`. Possibly zero new graph logic needed at all, which is worth validating before writing a bespoke Helm parser.
- **Ansible** — the real impedance mismatch of the five. Playbooks describe *sequential automation steps* (tasks, handlers, roles), not standing infrastructure resources — "a Component with relationships" doesn't map cleanly onto "do this, then this." Likely needs the model to grow a task/step-oriented component type and an ordering-flavored relationship type (`runs_before`, not `depends_on`) — flagged here as a model question for whoever picks this up, not assumed to just fit.
- **Pulumi** — the hardest. It's infrastructure-as-*code* (real Python/TypeScript/Go programs), so static text parsing fundamentally can't reliably determine what resources a Pulumi program creates — that requires actually *running* it (`pulumi preview` or equivalent) and reading its output. A different strategy from every parser InfraLens has today, worth a design pass of its own rather than assuming it slots into the current `InfrastructureParser` interface unchanged.

---

## 7. Performance

**Duplicate nodes.** Already solved upstream: Phase 3 engineered `Component.id` to be globally unique *by construction* (`docker:{relative_path}`, `compose:{relative_path}:{service}`, etc. — collision-safe even across multiple files of the same technology in a monorepo). `Graph.add_node()` just needs to be idempotent (last-write-wins on a dict keyed by id, or — if using NetworkX — its native `add_node()` behavior, which already treats a repeated id as an update, not a duplicate). No new ID scheme, no dedup pass needed.

**Efficient construction.** The baseline mapping (§3.1) is one pass over components + one pass over relationships — O(V+E), which is unavoidable and already optimal. The risk is entirely in §3.4's inference step: naive "compare every component against every other component" correlation is O(V²), and *that's* where a large monorepo could genuinely hurt. The fix is standard and should be designed in from day one, not retrofitted: build small lookup indexes first (e.g. `build_context_path → Dockerfile component`, `image_string → components declaring it`) in a single pass, then do inference as O(1) lookups per component instead of nested loops.

**Large repositories.** Reassuring baseline: the graph's size is bounded by *infrastructure-relevant files*, not repository size — even a huge codebase typically has a comparatively small number of Dockerfiles/Compose/Terraform/K8s files, so raw node/edge counts should stay modest even for sizable projects. The realistic "large" case is a genuine microservices monorepo with hundreds of services, each with its own manifests — worth designing for even if it's not the common case:
- In-memory footprint at that scale (low thousands of nodes/edges) is trivial regardless of representation.
- **Response payload size** is the more likely real bottleneck, once the graph is serialized back to a client — flagged for §8/Phase 8: an API that returns the *full* graph unconditionally will eventually need a summarized/collapsed view (grouped by directory or technology, with drill-down) rather than always shipping every node.
- Recommend a defensive **construction timeout or node-count ceiling** on the inference step specifically, mirroring the project's existing pattern of bounding potentially-slow operations (`CLONE_TIMEOUT_SECONDS` in `git_service.py`, the broad `except Exception` in `ikm_service.py`) — so a pathological repo can't hang a request indefinitely.

---

## 8. Future Integration

**Phase 5 — AI Explanation Engine.** Gets a *queryable structure* instead of a flat dump to summarize blindly. Concretely: start from "entry point" nodes (Ingress, exposed-port containers) found via `find_nodes()`, walk outward via dependency traversal / `topological_order()`, and generate narrative *in dependency order* — "an Nginx ingress routes to a FastAPI backend, which depends on a PostgreSQL database" reads naturally because it follows the graph's actual edges, not because an LLM had to reconstruct that structure from a flat list itself. `impact_analysis()` directly answers user questions like "what would changing the database affect" without any new logic — Phase 5 can be, largely, a natural-language layer over `GraphEngine`'s existing structured results.

**Phase 6 — Security Analysis.** Built *entirely* as a consumer of generic graph operations, contributing zero graph-specific code of its own — the same separation-of-concerns already enforced between parsers and the graph:
- `shortest_path()` from externally-reachable nodes to sensitive ones (Database, Secret) = attack-surface / blast-radius analysis.
- `detect_cycles()` results double as a configuration-hygiene signal.
- `connected_components()` surfaces orphaned/forgotten resources — a hygiene issue with real security relevance (unused infrastructure is unmonitored infrastructure).
- Inferred edges (§3.4) with `confidence: "heuristic"` are exactly the ones Security Analysis should be most careful weighting or should exclude outright from anything risk-scored.

**Phase 7 — Cloud Insights (cost).** `find_nodes(technology="terraform")` (or `cloudformation`, later) enumerates cloud resources by their already-captured `metadata.resource_type`, which an external pricing lookup can key off — again, the Graph Engine exposes generic filtering; it never needs to know what an `aws_instance` costs. The graph's *structure* adds a second, non-obvious insight for free: a Terraform resource with no relationships to anything else (via `connected_components()`) is a plausible signal of an orphaned, still-billing resource nobody's using — a cost-hygiene finding built entirely from a generic graph capability.

**Phase 8 — Interactive Frontend Visualization.** The most direct consumer: `GraphModel.to_model()` output (`{nodes, edges}`) is already shaped to match the conventions most JS graph-visualization libraries (React Flow, Cytoscape.js, vis.js) expect — `id`/`source`/`target` field names weren't arbitrary choices in §2, they were chosen with this consumer in mind. The performance discussion in §7 (payload size, summarized/collapsed views) is primarily *for* this phase — an interactive graph UI is exactly where "give me only the Kubernetes layer" or "collapse this into a directory-level summary" filtering (via `find_nodes()` / a future subgraph-extraction method) becomes a real, immediate need rather than a hypothetical one.

---

## 9. Component Interaction Diagram

```
   api/v1/analyze.py  (or a future dedicated caller)
         │
         │ calls
         ▼
   ikm_service.build_infrastructure_model()  ──►  InfrastructureModel
         │
         │ passed to
         ▼
   graph_service.build_graph(model)                      app/services/graph_service.py
         │
         ▼
┌──────────────────────────────────────────────────┐
│                     app/graph/                       │
│                                                        │
│   InfrastructureModel                                 │
│         │                                              │
│         ▼                                              │
│   GraphBuilder.build()                                  │
│     ├─ map Components → Nodes            (§3.1)          │
│     ├─ map Relationships → Edges          (§3.1)          │
│     ├─ refine node types                  (§3.3, table)   │
│     └─ infer additional edges              (§3.4)          │
│         │   selector matching · build-context ·           │
│         │   image correlation — each tagged with           │
│         │   origin + confidence                             │
│         ▼                                                 │
│   Graph  (adjacency-indexed working representation)         │
│         │                                                   │
│         ▼                                                   │
│   GraphEngine(graph)                                          │
│     ├─ query methods         → app/graph/algorithms/            │
│     ├─ traversal methods       traversal.py / cycles.py /        │
│     └─ algorithm methods       paths.py / components.py           │
└──────────────────────────────────────────────────┘
         │
         │ GraphEngine instance
         ▼
   graph_service returns it to the caller
         │
         ├──► api layer: .to_model() → GraphModel → JSON response
         └──► Phase 5/6/7/8 modules: hold the GraphEngine directly,
              call its query/algorithm methods as needed
```

---

## 10. Recommended Folder Structure

```
infralens/
└── app/
    ├── models/
    │   ├── schemas.py                  (existing — API request/response)
    │   ├── ikm.py                       (existing — Phase 3)
    │   └── graph.py                     [NEW]  Node, Edge, GraphModel — wire format, Pydantic
    │
    ├── parsers/                          (existing, Phase 3 — untouched)
    │
    ├── graph/                            [NEW package — the engine itself]
    │   ├── __init__.py
    │   ├── builder.py                    GraphBuilder: InfrastructureModel -> Graph
    │   ├── core.py                        Graph: internal container + adjacency index
    │   ├── engine.py                       GraphEngine: the public facade (§5.2)
    │   ├── inference.py                    the 3 inference rules from §3.4, + lookup tables
    │   ├── refinement.py                    the type-refinement table from §3.3
    │   ├── exceptions.py                     e.g. NodeNotFoundError
    │   └── algorithms/
    │       ├── __init__.py
    │       ├── traversal.py                 dependency traversal (BFS/DFS)
    │       ├── cycles.py                     cycle detection + topological sort
    │       ├── paths.py                       shortest path
    │       └── components.py                  connected components + impact analysis
    │
    └── services/
        ├── ... (existing, untouched)
        └── graph_service.py            [NEW, thin]  the one entry point api/ ever calls (§5.1)
```

Every new file has exactly one job, and the split between `models/graph.py` (data shape) and `graph/` (behavior) is the same models-vs-services split the project already uses everywhere else — nothing here is a new pattern, just the established one applied to a new concern.

---

## 11. Recommended Class Structure

```
                    ┌───────────────────────┐
                    │  InfrastructureParser   │   (existing, Phase 3, ABC)
                    └───────────┬─────────────┘
                                │ implements
          ┌─────────┬──────────┼───────────┬─────────────┐
          ▼         ▼          ▼           ▼             │
      Docker    Compose    Terraform   Kubernetes         │
      Parser     Parser      Parser      Parser            │
                                                             │
     ┌──────────────┐          ┌──────────────┐            │
     │  Component     │          │  Relationship  │  (existing, Phase 3, Pydantic)
     └──────┬─────────┘          └──────┬─────────┘            │
            │  compose                   │  compose                │
            └─────────────┬──────────────┘                          │
                          ▼                                          │
              ┌─────────────────────┐                                │
              │ InfrastructureModel    │  (existing, Phase 3)              │
              └──────────┬────────────┘                                    │
                          │ input to                                        │
                          ▼                                                  │
              ┌─────────────────────┐                                        │
              │   GraphBuilder          │  [NEW]                                  │
              │  + build(model) -> Graph │                                          │
              └──────────┬────────────┘                                              │
                          │ produces                                                   │
                          ▼                                                             │
              ┌─────────────────────┐        ┌──────────────┐   ┌──────────────┐         │
              │   Graph                 │───────▶│  Node          │   │  Edge          │  [NEW,
              │  - nodes: dict[id,Node]  │        │  (Pydantic)     │   │  (Pydantic)     │   app/models/graph.py]
              │  - edges: list[Edge]      │        └──────────────┘   └──────────────┘
              │  - adjacency index         │
              └──────────┬────────────────┘
                          │ wrapped by
                          ▼
              ┌─────────────────────────────┐
              │        GraphEngine              │  [NEW — the public facade, §5.2]
              │  + get_node / find_nodes           │
              │  + get_dependencies / get_dependents│
              │  + detect_cycles                     │
              │  + shortest_path                      │
              │  + connected_components                │
              │  + topological_order                    │
              │  + impact_analysis -> ImpactReport        │
              │  + to_model -> GraphModel                  │
              └──────────┬─────────────────────────────────┘
                          │ delegates algorithm calls to
          ┌───────────────┼───────────────┬────────────────┐
          ▼               ▼               ▼                ▼
   algorithms/      algorithms/     algorithms/       algorithms/
   traversal.py       cycles.py       paths.py          components.py
```

---

## 12. Design Decisions & Reasoning

1. **`app/graph/` as its own package, not another `services/*.py` file.** The internal complexity (multiple algorithms, an inference step, likely a graph library) justifies real sub-structure — same reasoning that gave Phase 3 its `parsers/` package instead of one big file.
2. **Two graph representations, not one** (§2.1) — a serialization-friendly wire format and an indexed working representation, bridged by `GraphEngine.to_model()`. Trying to serve both needs from one shape means a bad compromise on at least one of them.
3. **Facade pattern for `GraphEngine`** — one cohesive class for every consumer to hold and mock, internally delegating to focused single-purpose modules. Directly mirrors `ikm_service.py`'s existing role as a thin dispatcher over four parser classes.
4. **`Node.id` reuses `Component.id` verbatim.** Free traceability back to source, and the global-uniqueness guarantee Phase 3 already engineered is inherited rather than re-solved.
5. **Directed graph, always.** Every current relationship type is inherently directional; an undirected model would silently break ancestor/descendant semantics that impact analysis and cycle detection both depend on.
6. **Confidence/provenance metadata on every edge** (§3.5). Cheap to add, and it's what lets Phase 5 hedge language and Phase 6 exclude heuristic edges from anything risk-scored — without it, an inferred guess and a directly-parsed fact would be indistinguishable to every downstream consumer.
7. **The Graph Engine stays domain-agnostic** — no knowledge of "security," "cost," or "AI" anywhere in `app/graph/`. Same separation-of-concerns principle already stated explicitly in Phase 3's parser design, now enforced one layer up.
8. **Ephemeral, per-request construction — no persistence.** Consistent with the no-database posture since Phase 1. Explicitly named as a decision (not a default nobody chose) because it constrains what's realistic for parametrized cross-request queries — see §5.4 and §13.
9. **Type refinement and inference are table/config-driven, not branching code** (§3.3). What makes §6's extensibility claim actually true rather than aspirational — a new technology's refinement rule is a data addition.
10. **Recommend NetworkX as the underlying library, fully hidden behind `GraphEngine`.** Detailed next, in §13 — the short version is that encapsulation is what makes this a safe choice: nothing outside `app/graph/` would need to change if that choice were ever revisited.

---

## 13. Trade-offs Considered

**NetworkX vs. a hand-rolled graph.** This is the biggest one, so it gets full treatment rather than a one-liner.

*For NetworkX:* battle-tested implementations of exactly the requested algorithms (cycle detection, shortest path, connected components, topological sort, ancestor/descendant queries) — all genuinely easy to get subtly wrong by hand (cycle detection in a directed graph has real edge-case traps around back-edges vs. cross-edges; "shortest path" has different correct answers depending on whether the graph is weighted). It's pure-Python, lightweight to add, and directed multigraphs (needed since two components could plausibly have more than one relationship type between them) are natively supported.

*For hand-rolling:* zero new dependency, full control over the exact API shape, and — for a project explicitly built to demonstrate technical depth for internship interviews — real, first-hand evidence of DS&A competency (BFS/DFS, cycle detection, Dijkstra-or-BFS shortest path, union-find or flood-fill connected components) rather than "I imported a library."

*Recommendation:* NetworkX, encapsulated entirely behind `GraphEngine` (§5.2) so it's an implementation detail, not a public commitment. The domain-specific logic InfraLens actually needs — the IKM-to-graph mapping, type refinement, the three inference rules, `ImpactReport`'s shape — is *all* custom regardless of this choice, so "used a library" doesn't mean "there was no design work here." If the interview-technical-depth argument matters enough to outweigh the correctness/time savings, hand-rolling is a completely defensible alternative — but it should be a deliberate choice made with that trade-off in mind, not a default.

**Graph in every `/analyze` response vs. a separate endpoint.** Covered in §5.4 — leaning toward "same response, new field" for consistency with Phase 3's precedent, revisited if payload size becomes a real problem (§7).

**Eager inference vs. opt-in.** Running all three inference rules (§3.4) on every build vs. offering a "base graph" (parsed relationships only, fast) and an "enriched graph" (with inference, slower) as separate options. A quick frontend preview may not need inference; a security-analysis pass definitely does. Worth designing the builder's signature to support both from the start (e.g. an `infer: bool` flag) rather than retrofitting it once the performance cost of always-on inference is actually felt.

**Persisting graphs vs. always recomputing.** Named explicitly in §12 as a decision, not an oversight. Recomputing per-request is simpler and keeps the no-database posture intact; the cost is that any future "run 10 different queries against the same analysis" use case either re-sends the whole graph each time or needs some form of short-lived caching. Deferred until a concrete phase actually needs it (§5.4).

**Refined node types vs. passing IKM types through as-is.** Refinement (§3.3) adds a layer of mapping logic to maintain, but a graph where every Kubernetes object shows up as the same generic type is close to useless for the frontend's coloring/filtering needs — the added maintenance is worth it here.

---

## 14. Potential Pitfalls

- **Dangling relationship references.** If a `Relationship.source`/`.target` ever points at a component id that isn't in the model (a parser bug, or a relationship generated before a corresponding component exists), naive graph construction would either crash or silently create a broken edge. `GraphBuilder` needs to check both ends exist and skip (not crash on) anything that doesn't — the same "malformed input degrades gracefully" posture already used everywhere in `ikm_service.py` and the parsers.
- **Self-loops.** A component referencing itself should be handled deliberately, not by accident — most algorithms tolerate it fine, but cycle detection specifically needs to recognize a self-loop as a trivial one-node cycle rather than mishandling it.
- **O(V²) inference if the indexing in §7 isn't designed in from the start.** The single easiest way to accidentally make this whole system fall over on a large repo — worth stating as a pitfall precisely because it's the kind of thing that looks fine in every small test fixture and only shows up at real scale.
- **False-positive inferred edges misleading downstream consumers**, especially Security Analysis, if the confidence tagging from §3.5 is ever skipped "just this once" for a new inference rule. Worth treating as a hard rule for anyone extending `inference.py` later, not a suggestion.
- **Node-type proliferation as more technologies get added.** If every new parser invents its own bespoke types with no normalization, cross-technology queries and visualization degrade over time (a "container" concept fragmenting into `docker_container`, `k8s_container`, `nomad_task`, all conceptually the same thing to a user). Worth considering a lightweight `category` field alongside `node_type` (e.g. every workload-like node — regardless of technology — shares `category: "compute"`) before this becomes a real problem rather than after.
- **Frontend payload bloat**, covered in §7 — an API that always returns the complete graph works fine in every demo with a small test repo and becomes a real problem the first time someone points InfraLens at a genuine microservices monorepo.
- **Naming collision risk.** Three different things are all reasonably called "graph" in this system: the IKM's `components`/`relationships`, the wire-format `GraphModel`, and the internal `Graph` (or NetworkX object). The folder/naming scheme in §10 was chosen specifically to keep these distinguishable (`app/models/graph.py` vs. `app/graph/core.py`) — worth staying disciplined about as the codebase grows, since the confusion compounds quickly otherwise.

---

## 15. Final Recommended Architecture

- A new `app/graph/` package alongside the existing `parsers/` and `services/`, plus one new `app/models/graph.py` for the wire format — no existing file touched except adding one new service function (`graph_service.build_graph()`) that mirrors `ikm_service.build_infrastructure_model()` exactly.
- **`GraphBuilder`** converts the IKM into a graph in one pass: baseline Component→Node / Relationship→Edge mapping, then table-driven type refinement, then three specific, confidence-tagged inference rules (K8s selector matching, Compose build-context correlation, cross-technology image correlation) — nothing more speculative than that.
- **`GraphEngine`** is the one public facade every future phase talks to — directed graph, `NetworkX`-backed by recommendation but fully encapsulated, exposing dependency traversal, cycle detection, shortest path, connected components, topological sort, and impact analysis, plus generic filtered lookup.
- The graph stays **domain-agnostic** (no security/cost/AI knowledge inside it) and **ephemeral** (built fresh per request, no persistence) — both explicit, named decisions consistent with everything built in Phases 1–3, not accidents.
- Extensibility to Helm/Ansible/Pulumi/CloudFormation/Nomad is real, not aspirational, *because* of decisions already locked in during Phase 3 (the parser ABC, open `str`-typed IKM fields) — Phase 4's job was to make sure nothing here undoes that by hardcoding technology-specific logic into the graph layer itself.
- Performance risk is concentrated in exactly one place — inference — and the mitigation (index first, O(1) lookups, not O(V²) comparison) is designed in now rather than discovered later.
- Phases 5–8 each get a concrete, specific integration path already sketched in §8, all built entirely from `GraphEngine`'s generic public methods — none of them require the Graph Engine itself to grow new, phase-specific capabilities.
