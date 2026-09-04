"""Attack-surface analysis via GraphEngine reachability (Phase 9).

Detects an evidence-backed chain from an externally-reachable Kubernetes
entry point (a TLS-less Ingress, or a directly-exposed NodePort/
LoadBalancer Service) to a Secret component, using ONLY
GraphEngine.shortest_path() — the graph's own existing, canonical
representation of reachability. This module does not re-derive Service
selector -> workload pod-label matching itself; that correlation is
already the graph's inferred Service--connects_to-->Workload edge (see
app.graph.inference.infer_service_workload_edges(), confidence "high",
basis "label selector match") — duplicating that logic here would be
exactly the kind of parallel, drift-prone re-implementation this
project has avoided at every prior phase.

The chain this traverses, entirely via edges that already exist:

    Ingress --connects_to--> Service --connects_to--> Workload --uses--> Secret
       (parsed)                  (inferred, high confidence)      (parsed)
     -- or, for a directly-exposed Service (no Ingress hop needed) --
                          Service --connects_to--> Workload --uses--> Secret

A finding is emitted ONLY when GraphEngine.shortest_path() actually
returns a real path — never merely because metadata looks suggestive.
This module never mutates the graph and never mutates `components`.

SECRET SAFETY: only a Secret component's existence and identity
(id/name/metadata that already excludes data/stringData values, per
Phase 7F's own invariant) are ever touched here. No Secret data is
inspected, copied, or logged.
"""

from app.graph.engine import GraphEngine
from app.models.graph import Node
from app.models.ikm import Component
from app.security.rules import Severity, _finding
from app.explanation.evidence import Observation

_EXTERNALLY_REACHABLE_SERVICE_TYPES = frozenset({"NodePort", "LoadBalancer"})

# The Service->Workload hop is the ONLY inferred edge anywhere in this
# chain (see module docstring) -- identified generically by which node a
# hop starts FROM, so the wording stays correct regardless of whether
# the path starts at an Ingress (4 hops) or a directly-exposed Service
# (3 hops).
_INFERRED_HOP_SOURCE_KIND = "Service"


def _is_exposed_ingress(component: Component) -> bool:
    """A TLS-less Ingress. Strict `is False` -- an Ingress with unknown/
    absent has_tls is never possible (the parser always sets this field,
    see kubernetes_parser.py), but strict comparison is used regardless,
    on principle, matching every other rule's boolean-strictness in this
    project."""
    return (
        component.technology == "kubernetes"
        and component.metadata.get("kind") == "Ingress"
        and component.metadata.get("has_tls") is False
    )


def _is_exposed_service(component: Component) -> bool:
    """A Service the manifest itself declares externally reachable.
    ClusterIP (the default) and ExternalName (a DNS redirect, not a
    workload-exposure mechanism at all) are both correctly excluded by
    this membership check -- neither is ever in
    _EXTERNALLY_REACHABLE_SERVICE_TYPES."""
    return (
        component.technology == "kubernetes"
        and component.metadata.get("kind") == "Service"
        and component.metadata.get("service_type") in _EXTERNALLY_REACHABLE_SERVICE_TYPES
    )


def _is_secret(component: Component) -> bool:
    return component.technology == "kubernetes" and component.metadata.get("kind") == "Secret"


def _describe_hop(from_node: Node, to_node: Node) -> str:
    """One hop of the path, labeled with its real provenance -- parsed
    for every hop except the Service->Workload one, which is labeled as
    the inferred, high-confidence label-selector match it actually is.
    Never claims a hop is parsed when it isn't."""
    is_inferred_hop = from_node.metadata.get("kind") == _INFERRED_HOP_SOURCE_KIND
    provenance = "inferred, high-confidence label-selector match" if is_inferred_hop else "parsed"
    from_label = f"{from_node.metadata.get('kind', from_node.node_type)} '{from_node.name}'"
    to_label = f"{to_node.metadata.get('kind', to_node.node_type)} '{to_node.name}'"
    return f"{from_label} -> {to_label} ({provenance})"


def _build_reason(entry: Component, secret: Component, path: list[Node]) -> str:
    hops = "; ".join(_describe_hop(path[i], path[i + 1]) for i in range(len(path) - 1))
    entry_kind = entry.metadata.get("kind")
    return f"{entry_kind} '{entry.name}' has a graph path to Secret '{secret.name}': {hops}."


def check_attack_surface(components: list[Component], graph_engine: GraphEngine) -> list[Observation]:
    """Every real, GraphEngine-confirmed exposed-entry-to-Secret path,
    as Observation objects (kind=SECURITY_FINDING, rule_id
    "EXPOSED_PATH_TO_SECRET", severity HIGH) -- one per (exposed entry,
    reachable Secret) pair that GraphEngine.shortest_path() actually
    confirms, attached to the exposed entry-point component.

    Never emits a finding from metadata alone: if
    graph_engine.shortest_path(entry.id, secret.id) returns None, no
    finding is produced for that pair, regardless of how suggestive the
    surrounding metadata looks (e.g. an exposed Service whose selector
    matches nothing, or a workload that doesn't actually reference any
    Secret, both correctly and silently produce zero paths and
    therefore zero findings).

    Deterministically ordered by (entry.id, secret.id) -- computed
    before any Observation is constructed, so the order is stable
    regardless of `components`' own iteration order, and independent of
    app.security.rules.run_security_rules()'s own ordering (the two
    lists are merged and re-sorted by the caller,
    app.services.security_service.get_security_findings()).

    Does not mutate `components` or the graph.
    """
    exposed_entries = [c for c in components if _is_exposed_ingress(c) or _is_exposed_service(c)]
    secrets = [c for c in components if _is_secret(c)]

    reachable_pairs: list[tuple[Component, Component, list[Node]]] = []
    for entry in exposed_entries:
        for secret in secrets:
            path = graph_engine.shortest_path(entry.id, secret.id)
            if path is not None:
                reachable_pairs.append((entry, secret, path))

    reachable_pairs.sort(key=lambda pair: (pair[0].id, pair[1].id))

    return [
        _finding(
            rule_id="EXPOSED_PATH_TO_SECRET",
            severity=Severity.HIGH,
            title="Externally reachable path to a Secret",
            reason=_build_reason(entry, secret, path),
            component=entry,
        )
        for entry, secret, path in reachable_pairs
    ]
