"""Stage 5A: tests for app/models/explanation.py.

Covers ExplanationRequest's "exactly one input shape" validation and
basic construction of ExplanationResult / Confidence.
"""

import pytest
from pydantic import ValidationError

from app.models.explanation import Confidence, ExplanationRequest, ExplanationResult


# --- ExplanationRequest: valid shapes ---------------------------------------


def test_request_accepts_node_id_alone() -> None:
    request = ExplanationRequest(node_id="backend")
    assert request.node_id == "backend"
    assert request.source_id is None
    assert request.target_id is None


def test_request_accepts_source_and_target_together() -> None:
    request = ExplanationRequest(source_id="backend", target_id="database")
    assert request.node_id is None
    assert request.source_id == "backend"
    assert request.target_id == "database"


# --- ExplanationRequest: invalid shapes -------------------------------------


def test_request_rejects_neither_input() -> None:
    with pytest.raises(ValidationError):
        ExplanationRequest()


def test_request_rejects_both_node_id_and_source_id() -> None:
    with pytest.raises(ValidationError):
        ExplanationRequest(node_id="backend", source_id="backend")


def test_request_rejects_both_node_id_and_target_id() -> None:
    with pytest.raises(ValidationError):
        ExplanationRequest(node_id="backend", target_id="database")


def test_request_rejects_source_id_without_target_id() -> None:
    with pytest.raises(ValidationError):
        ExplanationRequest(source_id="backend")


def test_request_rejects_target_id_without_source_id() -> None:
    with pytest.raises(ValidationError):
        ExplanationRequest(target_id="database")


# --- ExplanationResult / Confidence ------------------------------------------


def test_result_constructs_with_template_generation() -> None:
    result = ExplanationResult(
        explanation="backend depends_on database.",
        confidence=Confidence.HIGH,
        generation_method="template",
        provider_name=None,
    )
    assert result.confidence == Confidence.HIGH
    assert result.generation_method == "template"
    assert result.provider_name is None
    assert result.evidence == []


def test_result_defaults_evidence_to_empty_list() -> None:
    result = ExplanationResult(
        explanation="No relationship found.",
        confidence=Confidence.LOW,
        generation_method="template",
    )
    assert result.evidence == []


def test_confidence_enum_values() -> None:
    assert Confidence.HIGH == "high"
    assert Confidence.MIXED == "mixed"
    assert Confidence.LOW == "low"
