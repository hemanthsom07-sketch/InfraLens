# InfraLens — Phase 4

AI-powered Infrastructure Analysis Platform. Give it a public GitHub
repository URL and it clones the repo, scans its contents, and reports
back the file count, directory tree, detected languages, application
frameworks, infrastructure/DevOps tooling, a structured
**Infrastructure Knowledge Model (IKM)**, and now a queryable
**dependency graph** — built from the IKM, with inferred relationships
(Kubernetes Service→workload via label selectors, Compose→Dockerfile,
cross-technology image correlation) on top of what was directly parsed.

- **Phase 1 (done): repository ingestion and scanning**
- **Phase 2 (done): technology detection**
- **Phase 3 (done): infrastructure understanding (IKM)**
- **Phase 4 (done): Graph Engine** — `graph` field: nodes, edges,
  dependency traversal, cycle detection, shortest path, connected
  components, impact analysis. See
  `infralens-phase4-graph-engine-architecture.md` for the full design;
  this phase is the implementation of that document.

AI-generated explanations, security analysis, and cloud cost insights are
planned for later phases — all designed to consume the Graph Engine's
public `GraphEngine` API (see `app/graph/engine.py`), not the IKM directly.

## Tech stack

- Python 3.11+
- FastAPI
- Pydantic v2
- uv (dependency management)
- `git` CLI (must be installed and on PATH — used to clone repositories)

No database, no auth, no Docker, no AI calls in this phase.

## Project structure

```
infralens/
├── app/
│   ├── main.py                            # FastAPI app, exception handlers, router registration
│   ├── api/v1/analyze.py                  # POST /api/v1/analyze
│   ├── core/config.py                     # Language map, ignored directories
│   ├── models/
│   │   ├── schemas.py                     # API request/response models
│   │   ├── ikm.py                         # Infrastructure Knowledge Model (Phase 3)
│   │   └── graph.py                       # Phase 4: Node, Edge, GraphModel, ImpactReport (wire format)
│   ├── parsers/                           # Phase 3: one independent parser per technology
│   │   ├── base.py
│   │   ├── docker_parser.py
│   │   ├── compose_parser.py
│   │   ├── terraform_parser.py
│   │   └── kubernetes_parser.py           # extended in Phase 4: captures selector/pod_labels too
│   ├── graph/                             # Phase 4: the Graph Engine
│   │   ├── core.py                        # Graph — internal NetworkX-backed container
│   │   ├── builder.py                     # GraphBuilder — IKM -> Graph
│   │   ├── engine.py                      # GraphEngine — THE public interface
│   │   ├── refinement.py                  # table-driven node-type refinement
│   │   ├── inference.py                   # the 3 approved inference rules
│   │   ├── exceptions.py                  # NodeNotFoundError
│   │   └── algorithms/
│   │       ├── traversal.py               # get_dependencies / get_dependents
│   │       ├── cycles.py                  # cycle detection + topological sort
│   │       ├── paths.py                   # shortest path
│   │       └── components.py              # connected components + impact analysis
│   └── services/
│       ├── git_service.py
│       ├── scanner_service.py
│       ├── framework_service.py
│       ├── infrastructure_service.py      # also exports is_dockerfile() etc., reused by ikm_service AND graph builder dispatch
│       ├── ikm_service.py
│       └── graph_service.py               # Phase 4: the one entry point api/ calls
├── docs-phase4-architecture.md            # the approved design this phase implements
├── pyproject.toml
├── .python-version
└── .gitignore
```

## Setup

```bash
uv sync
```

This creates a `.venv/` and installs dependencies from `pyproject.toml`.

## Run

```bash
uv run uvicorn app.main:app --reload
```

- API root: http://127.0.0.1:8000/
- Swagger UI: http://127.0.0.1:8000/docs
- OpenAPI schema: http://127.0.0.1:8000/openapi.json

## Example request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/octocat/Hello-World"}'
```

## Example response

```json
{
  "repository": "my-fastapi-app",
  "total_files": 42,
  "languages": ["Python", "Markdown"],
  "frameworks": ["FastAPI"],
  "infrastructure": ["Docker", "GitHub Actions"],
  "infrastructure_model": {
    "components": [
      {
        "id": "docker:Dockerfile",
        "name": "Dockerfile",
        "type": "container",
        "technology": "docker",
        "metadata": {
          "base_image": "python:3.12-slim",
          "workdir": "/app",
          "exposed_ports": [8000],
          "environment": {"PORT": "8000"},
          "cmd": ["fastapi", "run"]
        }
      }
    ],
    "relationships": []
  },
  "tree": [ ... ]
}
```

## Infrastructure Knowledge Model (Phase 3)

`infrastructure` (Phase 2) says *what's present* — a flat list of names.
`infrastructure_model` says *what it is* — actual components (with
extracted details) and the relationships between them, in one
technology-agnostic shape:

- **Component**: `id`, `name`, `type` (e.g. `container`, `service`,
  `terraform_resource`, `kubernetes_resource`), `technology`, and a
  free-form `metadata` dict with whatever that technology's parser found.
- **Relationship**: `source` / `target` (Component ids) + a
  `relationship_type` (currently only Compose's `depends_on` is
  generated — see below).

| Parser | Reads | Produces |
|---|---|---|
| `docker_parser.py` | `Dockerfile`, `Dockerfile.*` | One `container` component: base image (last stage, for multi-stage builds), workdir, exposed ports, environment (both `ENV` forms), COPY instructions, entrypoint, cmd |
| `compose_parser.py` | `docker-compose.yml`/`.yaml` | One `service` component per service (image, build context, ports, environment — both list and mapping forms, volumes) + a `depends_on` relationship per dependency |
| `terraform_parser.py` | `*.tf` | One `terraform_resource` component per `resource "type" "name" {}` block, any provider |
| `kubernetes_parser.py` | `*.yaml`/`.yml` manifests | One `kubernetes_resource` component per document (files can hold several `---`-separated documents) for Deployment, Service, ConfigMap, Secret, Ingress, StatefulSet — kind, name, container images, ports |

Design choices worth knowing about:

- **No cross-resource relationships from the Kubernetes parser** (e.g.
  linking a Service to the Deployment it fronts). That needs matching a
  Service's label selector against Pod template labels — cross-resource,
  K8s-specific reasoning that's a better fit for the future Graph Engine
  phase than for a parser that's only supposed to read its own file.
- **Terraform parsing is regex-based, not a full HCL parser.** Phase 3
  only asks for resource type + name, and a `resource "type" "name" {`
  header is simple and regular enough that a real HCL grammar wouldn't
  extract anything a regex doesn't already get here.
- **PyYAML is now a direct dependency** (Compose and Kubernetes need real
  structural parsing, not just presence-detection) — it was already being
  installed transitively by `uvicorn[standard]`, so `uv sync` doesn't
  change what's on disk, just makes the dependency explicit since the
  code now imports it directly.
- **Only `docker-compose.yml`/`.yaml` is recognized** (matching the spec
  exactly), not the newer prefix-free `compose.yaml` convention. This is
  a real gap: [docker/awesome-compose](https://github.com/docker/awesome-compose)
  — Docker's own example collection — uses `compose.yaml` exclusively, so
  running InfraLens on it finds 0 Compose services despite being full of
  them. Confirmed by cloning it and parsing a real file directly, which
  worked correctly once the parser was pointed at it — this is a filename
  detection gap, not a parsing one. One-line fix if wanted: add
  `"compose.yml"` / `"compose.yaml"` to `_COMPOSE_FILENAMES` in
  `infrastructure_service.py`.

## What's detected

**Frameworks** — from dependency/config files, at any depth (so a
monorepo with separate `backend/` and `frontend/` manifests works):

| Ecosystem | Manifest(s)                        | Recognizes                                             |
|-----------|-------------------------------------|----------------------------------------------------------|
| Python    | `requirements.txt`, `pyproject.toml`| FastAPI, Django, Flask                                   |
| Node      | `package.json`                      | React, Next.js, Express, NestJS, Vue, Angular             |
| Java      | `pom.xml`                           | Spring Boot                                               |
| Go        | `go.mod`                            | Gin, Echo, Fiber *(not in the original spec — see note below)* |
| Rust      | `Cargo.toml`                        | Actix Web, Rocket, Axum *(same note)*                     |

**Infrastructure** — by filename/path pattern (Kubernetes manifests are
the one case that needs a peek at file content, since `.yaml`/`.yml` on
its own is ambiguous):

- Docker (`Dockerfile`, `Dockerfile.*`)
- Docker Compose (`docker-compose.yml` / `.yaml`)
- Terraform (`*.tf`)
- Kubernetes (`.yaml`/`.yml` files declaring `apiVersion` + `kind`)
- Helm (`Chart.yaml`)
- GitHub Actions (anything under `.github/workflows/`)
- Nginx (`nginx.conf`)

## Notes / current limitations

- **QA fix (relationship generation):** an earlier QA pass found
  `infrastructure_model.relationships` was often `[]`. Root cause: this
  was **by design** for Kubernetes (Service→Deployment correlation lives
  in the Graph Engine's inference layer, not the IKM) and for Terraform
  (the original spec only asked for resource type/name, nothing about
  references) — not a broken pipeline; `docker/getting-started` and
  `kubernetes/examples` already had real graph edges before this fix, via
  Phase 4's existing inference rules. The one genuine gap: a
  **Terraform-only repo had zero relationships and zero graph edges**,
  since Terraform never contributed to either mechanism. Fixed by adding
  relationship extraction to all three parsers — Compose now emits
  shared-network/shared-volume relationships (via the previously-unused
  `network`/`volume` component types); Terraform and Kubernetes both gained
  a `resolve_references()` function that cross-checks references (Terraform
  interpolation/`depends_on`; Kubernetes ConfigMap/Secret/Ingress→Service)
  against components declared anywhere in the repo, including different
  files — called once from `ikm_service.py` after every file is parsed,
  since a single `parse()` call only ever sees one file. Verified against
  the exact repos from the QA report: `hashicorp/terraform-guides` went
  from 0→144 relationships and 0→144 graph edges. Zero API schema changes;
  see `tests/` for the automated regression suite this fix added.

- Only public repositories are supported (no authentication).
- Clones are shallow (`--depth 1`) and always deleted after analysis, even
  if an error occurs mid-request.
- Language detection is based on file extension, not file content.
- The spec named specific frameworks for Python/Node/Java but only listed
  the manifest file for Go (`go.mod`) and Rust (`Cargo.toml`). Gin/Echo/
  Fiber and Actix Web/Rocket/Axum were added for parity with the other
  ecosystems — edit the `_GO_FRAMEWORKS` / `_RUST_FRAMEWORKS` dicts in
  `framework_service.py` to change this.
- Kubernetes *detection* (the `infrastructure` list) is still the
  dependency-free regex heuristic from Phase 2 — reliable in practice and
  unchanged, since rewriting working code without a reason wasn't the
  goal. Kubernetes *parsing* (`infrastructure_model`, Phase 3) uses real
  structural YAML parsing (`yaml.safe_load_all`), since extracting kind,
  name, images, and ports properly needs it.
- YAML parsing (Compose and Kubernetes) uses PyYAML's `safe_load`, which
  prevents arbitrary object construction/code execution from untrusted
  YAML — but doesn't defend against algorithmic-complexity "YAML bomb"
  inputs (deeply nested anchors causing excessive CPU/memory). Not a
  concern for a portfolio tool analyzing repos you choose to point it at;
  worth a parse timeout before ever accepting fully untrusted input.
- `node_modules`, `__pycache__`, `.venv`, and `venv` are skipped entirely
  during scanning (in addition to `.git`), so a committed dependency
  folder doesn't flood the file count or get misread as the project's own
  frameworks.
- No CORS middleware yet — not needed until a frontend on a different
  origin is introduced in a later phase.
