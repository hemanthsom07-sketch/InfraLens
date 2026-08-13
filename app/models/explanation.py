"""Pydantic models for the AI Explanation Engine (Phase 5).

Stage 5A defines only the request/result shapes and the confidence
vocabulary. It deliberately does NOT define what "evidence" looks like —
that's EvidencePackage, built against the graph in a later stage — so
this module has no dependency on app.graph or app.services.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Confidence(StrEnum):
    """How much an ExplanationResult should be trusted.

    Mirrors the provenance already present on graph edges
    (metadata.origin / metadata.confidence — see app/graph/inference.py):
    an explanation built entirely from parsed, directly-observed
    relationships is HIGH; one that leans on inferred relationships is
    MIXED or LOW depending on how heuristic those inferences were.
    """

    HIGH = "high"
    MIXED = "mixed"
    LOW = "low"


class ExplanationRequest(BaseModel):
    """Request to explain either a single node or the relationship
    between two nodes.

    Exactly one of the two input shapes must be provided:
    - `node_id` alone -> explain that node (what it is, what it depends
      on, what depends on it, its impact).
    - `source_id` and `target_id` together -> explain the relationship
      (if any) between those two specific nodes.

    Providing neither, or providing `node_id` together with either
    `source_id`/`target_id`, is a validation error — the request
    wouldn't unambiguously mean one thing or the other.
    """

    node_id: str | None = Field(
        default=None, description="Explain this single node. Mutually exclusive with source_id/target_id."
    )
    source_id: str | None = Field(
        default=None, description="Explain the relationship from this node. Requires target_id."
    )
    target_id: str | None = Field(
        default=None, description="Explain the relationship to this node. Requires source_id."
    )

    @model_validator(mode="after")
    def _check_exactly_one_input_shape(self) -> "ExplanationRequest":
        has_node = self.node_id is not None
        has_pair = self.source_id is not None or self.target_id is not None

        if not has_node and not has_pair:
            raise ValueError("Provide either node_id, or both source_id and target_id.")
        if has_node and has_pair:
            raise ValueError("Provide node_id OR source_id/target_id, not both.")
        if has_pair and (self.source_id is None or self.target_id is None):
            raise ValueError("Both source_id and target_id are required together.")

        return self


class ExplanationResult(BaseModel):
    """The output of the Explanation Engine (a later stage) for a single
    ExplanationRequest.
    """

    explanation: str = Field(..., description="The generated natural-language explanation.")
    confidence: Confidence = Field(..., description="How much this explanation should be trusted.")
    generation_method: str = Field(
        ...,
        description='How `explanation` was produced — "template" for deterministic fallback, "llm" once a real provider is used.',
    )
    provider_name: str | None = Field(
        default=None,
        description='The LLM provider that generated this explanation, or None when generation_method="template".',
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Structured evidence the explanation is grounded in. Left as a loose list of "
            "dicts in this stage; a later stage defines the concrete EvidencePackage/"
            "Observation shape this will actually hold."
        ),
    )
