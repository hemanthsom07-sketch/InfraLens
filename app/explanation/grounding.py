"""Grounding (Phase 5, Stage 5F).

Turns a Stage 5B EvidencePackage into a GroundingContext: the same
evidence, bucketed by provenance, in a plain machine-readable form meant
to be read by an LLM (app.explanation.prompts, next) rather than a
person (that's Stage 5C's job).

ARCHITECTURAL RULE: this module consumes EvidencePackage/Observation
objects only — the exact same Stage 5B output Stage 5C already consumes.
It never imports GraphEngine, never imports networkx, and never queries
the graph itself. Every line it produces is a direct rendering of one
Observation's existing fields; nothing is invented, re-derived, or
inferred here.

Why a separate module from fallback.py: fallback.py's prose is written
for a person reading a final answer. This module's output is written for
an LLM that needs to parse "what is fact, what is inference, and how
confident is the inference" unambiguously — closer to a labeled data
dump than a sentence. Different consumer, deliberately different
rendering, same underlying evidence.
"""

from pydantic import BaseModel, Field

from app.explanation.evidence import EvidencePackage, Observation, ObservationKind

# Dependency-type edges are narrated the same way regardless of bucket —
# see fallback.py for the identical constant and rationale. Kept as a
# separate copy (not imported from fallback.py) so grounding.py has no
# dependency on Stage 5C at all, matching each stage owning its own
# concern.
_DEPENDENCY_EDGE_TYPES = frozenset({"depends_on", "uses", "contains", "mounts"})


class GroundingContext(BaseModel):
    """The same EvidencePackage, bucketed by provenance into plain,
    LLM-readable lines. Every string here traces back to exactly one
    Observation's fields — nothing is added, reworded into a claim, or
    summarized away.
    """

    subject_ids: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list, description="Parsed edges — safe to state as fact.")
    high_confidence_inferences: list[str] = Field(
        default_factory=list, description="Inferred edges, confidence=high — state as inferred, not certain."
    )
    heuristic_inferences: list[str] = Field(
        default_factory=list, description="Inferred edges, confidence=heuristic — weak signal, hedge strongly."
    )
    other_observations: list[str] = Field(
        default_factory=list,
        description=(
            "Everything without edge-level provenance (dependency/dependent lists, impact "
            "totals, cycles, indirect paths, no-relationship, graph summaries) — these come "
            "from deterministic graph algorithms, not edge inference, so there's no "
            "origin/confidence to bucket them by."
        ),
    )


def _edge_type(observation: Observation) -> str:
    return observation.detail.get("edge_type", "connects_to")


def _line_for_edge_observation(observation: Observation) -> str:
    """One plain line for an edge-derived observation (DIRECT_RELATIONSHIP
    or CONNECTION): who, what relationship, to/from whom, and — when
    present — the basis for an inference. No hedging language here; the
    section heading it's placed under (by build_grounding_context) is
    what tells the LLM whether to treat it as fact or inference."""
    subject = observation.subject_id or "?"
    related = observation.related_id or "?"
    edge_type = _edge_type(observation)
    line = f"{subject} {edge_type} {related}"
    if observation.basis:
        line += f" (basis: {observation.basis})"
    return line


def _line_for_other_observation(observation: Observation) -> str:
    """One plain line for a non-edge observation: kind plus its detail
    payload, verbatim — this module doesn't summarize or interpret it."""
    subject = f" [{observation.subject_id}]" if observation.subject_id else ""
    related = f" -> {observation.related_id}" if observation.related_id else ""
    detail = ", ".join(f"{key}={value}" for key, value in observation.detail.items())
    detail_suffix = f": {detail}" if detail else ""
    return f"{observation.kind.value}{subject}{related}{detail_suffix}"


_EDGE_DERIVED_KINDS = frozenset({ObservationKind.DIRECT_RELATIONSHIP, ObservationKind.CONNECTION})


def build_grounding_context(package: EvidencePackage) -> GroundingContext:
    """Bucket every Observation in `package` by provenance.

    Bucketing rule, applied per observation:
    - DIRECT_RELATIONSHIP / CONNECTION with origin="parsed"   -> facts
    - DIRECT_RELATIONSHIP / CONNECTION with origin="inferred",
      confidence="high"                                        -> high_confidence_inferences
    - DIRECT_RELATIONSHIP / CONNECTION with origin="inferred",
      confidence="heuristic"                                   -> heuristic_inferences
    - anything else (no origin, or an edge-derived observation
      with an unrecognized origin/confidence combination)       -> other_observations,
      since only a recognized parsed/inferred-high/inferred-heuristic
      combination is grounds to call something a fact or a
      characterized inference; an unrecognized combination is
      reported as-is rather than guessed into the wrong bucket.
    """
    context = GroundingContext(subject_ids=list(package.subject_ids))

    for observation in package.observations:
        if observation.kind in _EDGE_DERIVED_KINDS and observation.origin is not None:
            line = _line_for_edge_observation(observation)
            if observation.origin == "parsed":
                context.facts.append(line)
            elif observation.origin == "inferred" and observation.confidence == "high":
                context.high_confidence_inferences.append(line)
            elif observation.origin == "inferred" and observation.confidence == "heuristic":
                context.heuristic_inferences.append(line)
            else:
                context.other_observations.append(_line_for_other_observation(observation))
        else:
            context.other_observations.append(_line_for_other_observation(observation))

    return context
