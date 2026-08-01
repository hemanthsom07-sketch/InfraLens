"""Table-driven node-type refinement — architecture doc §3.3.

The IKM's Component.type is deliberately coarse for Kubernetes: every
kind (Deployment, Service, ConfigMap, ...) shares "kubernetes_resource",
with the specific kind sitting in metadata["kind"]. That's the right call
for the IKM, but too coarse for a graph a frontend wants to color/filter
by type. This module refines it — as a lookup table, never as branching
if/elif logic, so a new technology's refinement rule is always a data
addition here, not a code change.
"""

from app.models.ikm import ComponentType

# (technology, metadata["kind"]) -> refined node_type.
_REFINEMENT_TABLE: dict[tuple[str, str], str] = {
    ("kubernetes", "Deployment"): ComponentType.CONTAINER,
    ("kubernetes", "StatefulSet"): ComponentType.CONTAINER,
    ("kubernetes", "Service"): ComponentType.SERVICE,
    ("kubernetes", "Ingress"): "ingress",
    ("kubernetes", "ConfigMap"): "config",
    ("kubernetes", "Secret"): "secret",
}


def refine_node_type(technology: str, component_type: str, metadata: dict) -> str:
    """Return a more specific node_type, or `component_type` unchanged if
    no refinement rule applies (e.g. Docker/Compose/Terraform components,
    or a Kubernetes kind not in the table)."""
    kind = metadata.get("kind")
    if kind is None:
        return component_type
    return _REFINEMENT_TABLE.get((technology, kind), component_type)
