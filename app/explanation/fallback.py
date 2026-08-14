"""Deterministic Fallback Explanations (Phase 5, Stage 5C).

Turns a Stage 5B EvidencePackage into a human-readable ExplanationResult
using fixed templates — no LLM, no randomness, no network calls. Same
EvidencePackage in, same ExplanationResult out, every time.

ARCHITECTURAL RULE: this module consumes EvidencePackage/Observation
objects only. It never imports GraphEngine, never imports networkx, and
never queries the graph itself — everything it says is already sitting
in the evidence it was handed. If a fact isn't in the EvidencePackage,
this module does not say it.

CRITICAL PROVENANCE RULE: a parsed relationship may be stated as fact.
An inferred relationship must always be worded as an inference — "may",
"InfraLens infers/suggests" — carrying its confidence band and basis,
never flattened into an unqualified "X depends on Y". This module is the
one place that turns Observation.origin/confidence/basis into English,
so getting this rule right here is what keeps every explanation type
honest about what it actually knows.
"""

from app.explanation.evidence import EvidencePackage, Observation, ObservationKind
from app.models.explanation import Confidence, ExplanationResult

# Dependency-type edges (see app/graph/algorithms/traversal.py) are already
# narrated by the dependency/dependent/impact sections below. The
# "architecture/connections" section exists to surface the *other* kind of
# relationship — lateral, connects_to-style — so it deliberately excludes
# these to avoid saying the same fact twice in different words.
_DEPENDENCY_EDGE_TYPES = frozenset({"depends_on", "uses", "contains", "mounts"})

# Two forms of the same vocabulary: present-tense ("X depends on Y.") for
# stating a parsed fact, base form ("X may depend on Y.") for hedging an
# inference. Using the present-tense form after "may" reads as broken
# English ("may connects to") and, more importantly, blurs the exact
# wording line that's supposed to separate a fact from an inference.
_EDGE_TYPE_PHRASES_PRESENT = {
    "depends_on": "depends on",
    "uses": "uses",
    "contains": "contains",
    "mounts": "mounts",
    "connects_to": "connects to",
}

_EDGE_TYPE_PHRASES_BASE = {
    "depends_on": "depend on",
    "uses": "use",
    "contains": "contain",
    "mounts": "mount",
    "connects_to": "connect to",
}


def _confidence_from_edge_observations(observations: list[Observation]) -> Confidence:
    """Confidence banding for a set of edge-derived observations
    (origin/confidence copied verbatim from Edge.metadata):

    - no edge-derived observations at all -> HIGH (nothing uncertain was said)
    - any heuristic-confidence inference present -> LOW
    - any inference present (high-confidence or better) -> MIXED
    - everything parsed -> HIGH
    """
    edge_observations = [o for o in observations if o.origin is not None]
    if not edge_observations:
        return Confidence.HIGH
    if any(o.origin == "inferred" and o.confidence == "heuristic" for o in edge_observations):
        return Confidence.LOW
    if any(o.origin == "inferred" for o in edge_observations):
        return Confidence.MIXED
    return Confidence.HIGH


def _sentence_for_edge_observation(observation: Observation, subject_name: str, related_name: str) -> str:
    """Word a single directed edge observation, honoring the provenance
    rule: parsed = stated as fact, inferred = explicitly hedged and
    carrying its confidence/basis."""
    edge_type = observation.detail.get("edge_type", "connects_to")

    if observation.origin == "parsed":
        phrase = _EDGE_TYPE_PHRASES_PRESENT.get(edge_type, edge_type.replace("_", " "))
        return f"{subject_name} {phrase} {related_name}."

    if observation.origin == "inferred":
        phrase = _EDGE_TYPE_PHRASES_BASE.get(edge_type, edge_type.replace("_", " "))
        basis = observation.basis or "available evidence"
        confidence = observation.confidence or "unknown"
        return (
            f"InfraLens infers that {subject_name} may {phrase} {related_name}, "
            f"based on {basis} (inferred, {confidence} confidence) — this is not a "
            f"directly declared relationship."
        )

    # No provenance recorded at all: state only the bare structural fact.
    phrase = _EDGE_TYPE_PHRASES_PRESENT.get(edge_type, edge_type.replace("_", " "))
    return f"{subject_name} {phrase} {related_name} (relationship type: {edge_type})."


def _evidence_payload(package: EvidencePackage) -> list[dict]:
    return [observation.model_dump(mode="json") for observation in package.observations]


# --- 1/2/3/4/6. Node explanation: what it is, dependencies, dependents, ------
# --- impact, and lateral connections ("architecture/connections") -----------


def explain_node(package: EvidencePackage) -> ExplanationResult:
    """Explain a single node from a build_node_evidence() EvidencePackage."""
    node_info = next((o for o in package.observations if o.kind == ObservationKind.NODE_INFO), None)
    if node_info is None:
        return ExplanationResult(
            explanation="No evidence is available to explain this component.",
            confidence=Confidence.LOW,
            generation_method="template",
            provider_name=None,
            evidence=_evidence_payload(package),
        )

    subject_id = node_info.subject_id or "This component"
    name = node_info.detail.get("name", subject_id)
    node_type = node_info.detail.get("node_type", "component")
    technology = node_info.detail.get("technology", "unknown technology")

    sentences = [f"{name} is a {node_type} component ({technology})."]

    dependencies = [o for o in package.observations if o.kind == ObservationKind.DEPENDENCY]
    if dependencies:
        names = ", ".join(o.detail.get("name", o.related_id) for o in dependencies)
        sentences.append(f"{name} depends on: {names}.")
    else:
        sentences.append(f"{name} has no known dependencies.")

    dependents = [o for o in package.observations if o.kind == ObservationKind.DEPENDENT]
    if dependents:
        names = ", ".join(o.detail.get("name", o.related_id) for o in dependents)
        sentences.append(f"The following depend on {name}: {names}.")
    else:
        sentences.append(f"Nothing currently depends on {name}.")

    impact_summary = next((o for o in package.observations if o.kind == ObservationKind.IMPACT_SUMMARY), None)
    if impact_summary is not None:
        total = impact_summary.detail.get("total_impact_count", 0)
        if total == 0:
            sentences.append(f"Changing {name} would have no direct or transitive impact on other components.")
        else:
            direct = [o for o in package.observations if o.kind == ObservationKind.IMPACT_DIRECT]
            transitive = [o for o in package.observations if o.kind == ObservationKind.IMPACT_TRANSITIVE]
            sentences.append(
                f"Changing {name} would impact {total} component(s) in total: "
                f"{len(direct)} directly and {len(transitive)} transitively."
            )

    lateral_connections = [
        o
        for o in package.observations
        if o.kind == ObservationKind.CONNECTION and o.detail.get("edge_type") not in _DEPENDENCY_EDGE_TYPES
    ]
    for observation in lateral_connections:
        related_name = observation.related_id or "another component"
        sentences.append(_sentence_for_edge_observation(observation, name, related_name))

    is_isolated = (
        not dependencies
        and not dependents
        and not lateral_connections
        and (impact_summary is None or impact_summary.detail.get("total_impact_count", 0) == 0)
    )
    if is_isolated:
        sentences.append(f"{name} appears to be isolated within the infrastructure graph.")

    confidence = _confidence_from_edge_observations(lateral_connections)

    return ExplanationResult(
        explanation=" ".join(sentences),
        confidence=confidence,
        generation_method="template",
        provider_name=None,
        evidence=_evidence_payload(package),
    )


# --- 5. Relationship explanation: direct, indirect, or none -----------------


def explain_relationship(package: EvidencePackage) -> ExplanationResult:
    """Explain the relationship between two nodes from a
    build_relationship_evidence() EvidencePackage."""
    source_id = package.subject_ids[0] if package.subject_ids else "the source component"
    target_id = package.subject_ids[1] if len(package.subject_ids) > 1 else "the target component"

    direct = [o for o in package.observations if o.kind == ObservationKind.DIRECT_RELATIONSHIP]
    if direct:
        sentences = [_sentence_for_edge_observation(o, source_id, target_id) for o in direct]
        return ExplanationResult(
            explanation=" ".join(sentences),
            confidence=_confidence_from_edge_observations(direct),
            generation_method="template",
            provider_name=None,
            evidence=_evidence_payload(package),
        )

    indirect = next((o for o in package.observations if o.kind == ObservationKind.INDIRECT_PATH), None)
    if indirect is not None:
        path = indirect.detail.get("path", [])
        hop_count = indirect.detail.get("hop_count", max(len(path) - 1, 0))
        path_description = " -> ".join(path) if path else f"{source_id} -> ... -> {target_id}"
        return ExplanationResult(
            explanation=(
                f"{source_id} has no direct relationship with {target_id}, but InfraLens found an "
                f"indirect path between them through {hop_count} step(s): {path_description}."
            ),
            confidence=Confidence.MIXED,
            generation_method="template",
            provider_name=None,
            evidence=_evidence_payload(package),
        )

    no_relationship = next((o for o in package.observations if o.kind == ObservationKind.NO_RELATIONSHIP), None)
    if no_relationship is not None:
        return ExplanationResult(
            explanation=f"No relationship — direct or indirect — was found between {source_id} and {target_id}.",
            confidence=Confidence.HIGH,
            generation_method="template",
            provider_name=None,
            evidence=_evidence_payload(package),
        )

    return ExplanationResult(
        explanation=f"No evidence is available to explain the relationship between {source_id} and {target_id}.",
        confidence=Confidence.LOW,
        generation_method="template",
        provider_name=None,
        evidence=_evidence_payload(package),
    )


# --- 7/8. Whole-graph observations and cycles --------------------------------


def explain_graph(package: EvidencePackage) -> ExplanationResult:
    """Explain the whole graph from a build_graph_evidence() EvidencePackage."""
    summaries = [o for o in package.observations if o.kind == ObservationKind.GRAPH_SUMMARY]
    cycles = [o for o in package.observations if o.kind == ObservationKind.CYCLE]

    if not summaries:
        return ExplanationResult(
            explanation="No evidence is available to explain this graph.",
            confidence=Confidence.LOW,
            generation_method="template",
            provider_name=None,
            evidence=_evidence_payload(package),
        )

    size_summary = summaries[0]
    node_count = size_summary.detail.get("node_count", 0)
    edge_count = size_summary.detail.get("edge_count", 0)

    if node_count == 0:
        return ExplanationResult(
            explanation="No infrastructure components were found to explain.",
            confidence=Confidence.LOW,
            generation_method="template",
            provider_name=None,
            evidence=_evidence_payload(package),
        )

    has_cycles = size_summary.detail.get("has_cycles", False)
    sentences = [
        f"This infrastructure graph has {node_count} component(s) and {edge_count} relationship(s).",
        (
            "It contains at least one dependency cycle."
            if has_cycles
            else "It has no dependency cycles."
        ),
    ]

    isolation_summary = summaries[1] if len(summaries) > 1 else None
    if isolation_summary is not None:
        isolated_ids = isolation_summary.detail.get("isolated_node_ids", [])
        component_count = isolation_summary.detail.get("connected_component_count")
        if isolated_ids:
            sentences.append(
                f"The following component(s) are isolated from the rest of the graph: "
                f"{', '.join(isolated_ids)}."
            )
        elif component_count is not None:
            sentences.append("Every component is connected to at least one other component.")

    breakdown_summary = summaries[2] if len(summaries) > 2 else None
    if breakdown_summary is not None:
        node_type_counts = breakdown_summary.detail.get("node_type_counts", {})
        technology_counts = breakdown_summary.detail.get("technology_counts", {})
        if node_type_counts:
            type_text = ", ".join(f"{count} {kind}" for kind, count in node_type_counts.items())
            sentences.append(f"Component types: {type_text}.")
        if technology_counts:
            tech_text = ", ".join(f"{count} {tech}" for tech, count in technology_counts.items())
            sentences.append(f"Technologies detected: {tech_text}.")

    for cycle in cycles:
        node_ids = cycle.detail.get("node_ids", [])
        sentences.append(f"A dependency cycle was detected involving: {', '.join(node_ids)}.")

    return ExplanationResult(
        explanation=" ".join(sentences),
        confidence=Confidence.HIGH,
        generation_method="template",
        provider_name=None,
        evidence=_evidence_payload(package),
    )


# --- Dispatcher ----------------------------------------------------------------


def generate_fallback_explanation(package: EvidencePackage) -> ExplanationResult:
    """Route an EvidencePackage to the right template based on how many
    subject_ids it carries — the same convention Stage 5B's builders use:
    0 -> whole-graph, 1 -> single node, 2 -> relationship between two nodes.
    """
    if len(package.subject_ids) == 0:
        return explain_graph(package)
    if len(package.subject_ids) == 1:
        return explain_node(package)
    if len(package.subject_ids) == 2:
        return explain_relationship(package)

    raise ValueError(
        f"Unsupported EvidencePackage shape: expected 0, 1, or 2 subject_ids, got {package.subject_ids!r}"
    )
