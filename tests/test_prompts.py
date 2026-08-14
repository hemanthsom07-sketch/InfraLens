"""Stage 5F: tests for app/explanation/prompts.py."""

from app.explanation.evidence import build_graph_evidence, build_node_evidence, build_relationship_evidence
from app.explanation.prompts import SYSTEM_PROMPT, build_prompt
from app.graph.engine import GraphEngine
from app.llm.models import LLMRequest
from app.models.ikm import Component, InfrastructureModel, Relationship


def _main_engine() -> GraphEngine:
    components = [
        Component(
            id="backend", name="backend", type="service", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml", "image": "myapp/backend:1.0"},
        ),
        Component(
            id="db", name="db", type="database", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
        Component(
            id="k8s-deploy", name="backend-deployment", type="kubernetes_resource", technology="kubernetes",
            metadata={"kind": "Deployment", "pod_labels": {"app": "backend"}, "images": ["myapp/backend:1.0"]},
        ),
        Component(
            id="k8s-svc", name="backend-service", type="kubernetes_resource", technology="kubernetes",
            metadata={"kind": "Service", "selector": {"app": "backend"}},
        ),
    ]
    relationships = [Relationship(source="backend", target="db", relationship_type="depends_on")]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _empty_engine() -> GraphEngine:
    return GraphEngine.from_infrastructure_model(InfrastructureModel(), infer=True)


# --- shape / provider-agnosticism --------------------------------------------


def test_build_prompt_returns_an_llm_request() -> None:
    result = build_prompt(build_node_evidence(_main_engine(), "backend"))
    assert isinstance(result, LLMRequest)
    assert result.system == SYSTEM_PROMPT
    assert isinstance(result.prompt, str) and result.prompt


# --- system prompt instructs staying within evidence -------------------------


def test_system_prompt_instructs_using_only_supplied_evidence() -> None:
    assert "only use the evidence" in SYSTEM_PROMPT
    assert "never invent, assume, or infer" in SYSTEM_PROMPT


def test_system_prompt_instructs_hedging_inferred_relationships() -> None:
    assert "never as certain" in SYSTEM_PROMPT
    assert "confidence level" in SYSTEM_PROMPT


def test_system_prompt_instructs_admitting_insufficient_evidence() -> None:
    assert "does not contain enough information to answer, say so" in SYSTEM_PROMPT


# --- rendered body reflects grounding buckets --------------------------------


def test_parsed_relationship_appears_under_facts_heading() -> None:
    request = build_prompt(build_relationship_evidence(_main_engine(), "backend", "db"))
    assert "FACTS" in request.prompt
    assert "backend depends_on db" in request.prompt


def test_heuristic_relationship_never_appears_under_facts_heading() -> None:
    request = build_prompt(build_relationship_evidence(_main_engine(), "backend", "k8s-deploy"))
    # This package has no parsed evidence at all, so FACTS shouldn't render.
    assert "FACTS" not in request.prompt
    assert "INFERRED, heuristic confidence" in request.prompt
    assert "image reference match (myapp/backend)" in request.prompt


def test_high_confidence_relationship_appears_under_its_own_heading() -> None:
    request = build_prompt(build_relationship_evidence(_main_engine(), "k8s-svc", "k8s-deploy"))
    assert "INFERRED, high confidence" in request.prompt
    assert "label selector match" in request.prompt
    assert "FACTS" not in request.prompt  # no parsed evidence in this package


def test_empty_sections_are_omitted_not_printed_empty() -> None:
    """A pure-fact package shouldn't print empty INFERRED headings."""
    request = build_prompt(build_relationship_evidence(_main_engine(), "backend", "db"))
    assert "INFERRED, high confidence" not in request.prompt
    assert "INFERRED, heuristic confidence" not in request.prompt


def test_task_instruction_is_present() -> None:
    request = build_prompt(build_node_evidence(_main_engine(), "backend"))
    assert "Using ONLY the evidence above" in request.prompt


def test_subjects_line_present_for_node_package() -> None:
    request = build_prompt(build_node_evidence(_main_engine(), "backend"))
    assert "Subjects: backend" in request.prompt


def test_subjects_line_for_graph_level_package() -> None:
    request = build_prompt(build_graph_evidence(_main_engine()))
    assert "Subjects: (whole graph)" in request.prompt


# --- empty graph --------------------------------------------------------------


def test_empty_graph_prompt_has_no_facts_or_inferred_headings() -> None:
    request = build_prompt(build_graph_evidence(_empty_engine()))
    assert "FACTS" not in request.prompt
    assert "INFERRED" not in request.prompt
    assert "OTHER OBSERVATIONS" in request.prompt


# --- determinism ---------------------------------------------------------------


def test_build_prompt_is_deterministic() -> None:
    engine = _main_engine()
    package = build_node_evidence(engine, "backend")
    first = build_prompt(package)
    second = build_prompt(package)
    assert first == second
