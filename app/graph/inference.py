"""The three approved inference rules — architecture doc §3.4. Nothing
beyond these three: inference is deliberately scoped, not open-ended
heuristic guessing.

Each function takes the full component list and returns the Edge objects
it infers, fully formed (including id and provenance metadata). Every
edge is tagged metadata["origin"] = "inferred" plus a "confidence" and a
"basis" string explaining why — so downstream consumers (Phase 5, Phase 6
especially) can weight or discount them appropriately.
"""

import posixpath

from app.models.graph import Edge
from app.models.ikm import Component, RelationshipType


def _inferred_edge(source: Component, target: Component, edge_type: str, confidence: str, basis: str) -> Edge:
    return Edge(
        id=f"{source.id}--{edge_type}-->{target.id}",
        source=source.id,
        target=target.id,
        edge_type=edge_type,
        metadata={"origin": "inferred", "confidence": confidence, "basis": basis},
    )


# --- Rule 1: Kubernetes Service -> Deployment/StatefulSet ------------------
# Not actually a heuristic: this is exactly how Kubernetes itself decides
# which Pods a Service routes traffic to (spec.selector vs. the pod
# template's metadata.labels), so it's tagged confidence="high". Scoped
# to same-namespace pairs only (Kubernetes never routes a Service's
# traffic across namespaces), so "high" stays an honest label rather than
# one that happens to be right only within a single namespace.

_K8S_WORKLOAD_KINDS = {"Deployment", "StatefulSet"}


def _selector_matches(selector: dict, labels: dict) -> bool:
    """True if every key/value in `selector` is also present in `labels`
    (Kubernetes' own equality-based label-selector matching semantics)."""
    if not selector:
        return False
    return all(labels.get(key) == value for key, value in selector.items())


def infer_service_workload_edges(components: list[Component]) -> list[Edge]:
    services = [c for c in components if c.technology == "kubernetes" and c.metadata.get("kind") == "Service"]
    workloads = [c for c in components if c.technology == "kubernetes" and c.metadata.get("kind") in _K8S_WORKLOAD_KINDS]

    edges: list[Edge] = []
    for service in services:
        selector = service.metadata.get("selector") or {}
        if not selector:
            continue
        service_namespace = service.metadata.get("namespace")
        for workload in workloads:
            # Namespace scoping: Kubernetes itself never routes a
            # Service's traffic to a Pod in a different namespace, so a
            # cross-namespace label match here would be a false positive
            # masquerading as confidence="high". "Unspecified" namespace
            # is its own equivalence class (see kubernetes_parser.py's
            # namespace-capture comment) — it matches another unspecified
            # namespace, never an explicit one.
            if workload.metadata.get("namespace") != service_namespace:
                continue
            pod_labels = workload.metadata.get("pod_labels") or {}
            if _selector_matches(selector, pod_labels):
                edges.append(
                    _inferred_edge(
                        service, workload, RelationshipType.CONNECTS_TO, "high", "label selector match"
                    )
                )
    return edges


# --- Rule 2: Compose service -> the Dockerfile it builds --------------------
# Path correlation: resolve the service's build_context relative to the
# compose file's own location, and check for a Dockerfile component there.


def _resolve_dockerfile_path(compose_source_file: str, build_context: str) -> str:
    """Resolve `build_context` (as written in the compose file, e.g.
    "./backend") against the directory containing `compose_source_file`
    (e.g. "infra/docker-compose.yml" -> "infra"), returning a normalized,
    repo-root-relative posix path to where a Dockerfile would sit."""
    base_dir = posixpath.dirname(compose_source_file)
    context_dir = posixpath.normpath(posixpath.join(base_dir, build_context))
    return posixpath.normpath(posixpath.join(context_dir, "Dockerfile"))


def infer_compose_dockerfile_edges(components: list[Component]) -> list[Edge]:
    compose_services = [c for c in components if c.technology == "docker-compose"]
    dockerfiles_by_path = {c.metadata["source_file"]: c for c in components if c.technology == "docker"}

    edges: list[Edge] = []
    for service in compose_services:
        build_context = service.metadata.get("build_context")
        if not build_context:
            continue
        candidate_path = _resolve_dockerfile_path(service.metadata["source_file"], build_context)
        dockerfile = dockerfiles_by_path.get(candidate_path)
        if dockerfile is not None:
            edges.append(
                _inferred_edge(service, dockerfile, RelationshipType.USES, "high", "build context path match")
            )
    return edges


# --- Rule 3: cross-technology image correlation -----------------------------
# The genuinely heuristic one: image reference strings are just strings,
# with no semantic guarantee two matching ones are "the same" deployable
# artifact — always tagged confidence="heuristic".


def _normalize_image(image: str) -> str:
    """Strip a digest and/or tag so e.g. "postgres:15" and "postgres:latest"
    still normalize to the same value, without stripping a registry
    host:port prefix (which also legitimately contains a colon)."""
    image = image.split("@", 1)[0]  # drop a digest, e.g. "...@sha256:..."
    if "/" in image:
        prefix, _, last_segment = image.rpartition("/")
        if ":" in last_segment:
            last_segment = last_segment.rsplit(":", 1)[0]
        return f"{prefix}/{last_segment}"
    if ":" in image:
        return image.rsplit(":", 1)[0]
    return image


def infer_image_correlation_edges(components: list[Component]) -> list[Edge]:
    compose_images = [
        (c, c.metadata["image"]) for c in components if c.technology == "docker-compose" and c.metadata.get("image")
    ]
    k8s_images = [
        (c, image) for c in components if c.technology == "kubernetes" for image in c.metadata.get("images", [])
    ]

    edges: list[Edge] = []
    for compose_component, compose_image in compose_images:
        normalized = _normalize_image(compose_image)
        for k8s_component, k8s_image in k8s_images:
            if _normalize_image(k8s_image) == normalized:
                edges.append(
                    _inferred_edge(
                        compose_component,
                        k8s_component,
                        RelationshipType.CONNECTS_TO,
                        "heuristic",
                        f"image reference match ({normalized})",
                    )
                )
    return edges


def infer_all_edges(components: list[Component]) -> list[Edge]:
    """Run all three approved rules and return every inferred edge."""
    return [
        *infer_service_workload_edges(components),
        *infer_compose_dockerfile_edges(components),
        *infer_image_correlation_edges(components),
    ]
