"""Stage 5D: tests for app/explanation/engine.py.

Reuses the same hand-crafted graph shape as tests/test_evidence.py and
tests/test_explanation_fallback.py (defined locally, not imported, so
this file stays self-contained), plus one additional fixture built
specifically to exercise the "parsed + high-confidence inferred mixed
together on the same node" confidence band, which the shared fixture
doesn't produce on its own.
"""

import pytest

from app.explanation.engine import ExplanationEngine
from app.explanation.evidence import build_node_evidence
from app.explanation.fallback import explain_node
from app.graph.engine import GraphEngine
from app.graph.exceptions import NodeNotFoundError
from app.llm.exceptions import LLMUnavailableError
from app.llm.models import LLMRequest, LLMResponse
from app.llm.provider import LLMProvider
from app.models.explanation import Confidence, ExplanationRequest
from app.models.ikm import Component, InfrastructureModel, Relationship


# --- fixtures (mirrors tests/test_evidence.py / test_explanation_fallback.py) --


def _main_engine() -> GraphEngine:
    """
    backend2 --depends_on--> backend --depends_on--> db
    k8s-svc  --connects_to--> k8s-deploy   (inferred, confidence=high)
    backend  --connects_to--> k8s-deploy   (inferred, confidence=heuristic)
    isolated                                (no edges at all)
    """
    components = [
        Component(
            id="backend",
            name="backend",
            type="service",
            technology="docker-compose",
            metadata={"source_file": "docker-compose.yml", "image": "myapp/backend:1.0"},
        ),
        Component(
            id="db", name="db", type="database", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
        Component(
            id="backend2", name="backend2", type="service", technology="docker-compose",
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
        Component(id="isolated", name="isolated", type="service", technology="docker-compose", metadata={}),
    ]
    relationships = [
        Relationship(source="backend", target="db", relationship_type="depends_on"),
        Relationship(source="backend2", target="backend", relationship_type="depends_on"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _cyclic_engine() -> GraphEngine:
    components = [
        Component(id="c1", name="c1", type="service", technology="docker-compose", metadata={}),
        Component(id="c2", name="c2", type="service", technology="docker-compose", metadata={}),
    ]
    relationships = [
        Relationship(source="c1", target="c2", relationship_type="depends_on"),
        Relationship(source="c2", target="c1", relationship_type="depends_on"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _mixed_confidence_engine() -> GraphEngine:
    """A node ("hub") touched by exactly one PARSED lateral edge and one
    HIGH-confidence INFERRED lateral edge, and nothing heuristic — the
    one combination that should band to Confidence.MIXED."""
    components = [
        Component(id="other", name="other", type="service", technology="docker-compose", metadata={}),
        Component(
            id="hub", name="hub", type="kubernetes_resource", technology="kubernetes",
            metadata={"kind": "Deployment", "pod_labels": {"app": "hub"}},
        ),
        Component(
            id="svc", name="svc", type="kubernetes_resource", technology="kubernetes",
            metadata={"kind": "Service", "selector": {"app": "hub"}},
        ),
    ]
    relationships = [
        Relationship(source="other", target="hub", relationship_type="connects_to"),  # parsed
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)  # svc -> hub inferred, high


# --- a minimal in-test-only LLM provider stub (never shipped, never real) ---


class _StubProvider(LLMProvider):
    def __init__(self, text: str = "stub explanation", name: str = "stub", available: bool = True) -> None:
        self._text = text
        self._name = name
        self._available = available
        self.generate_calls = 0

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.generate_calls += 1
        return LLMResponse(text=self._text, provider_name=self._name)


class _FlakyProvider(LLMProvider):
    """Reports itself available but always fails to generate — the
    "provider claimed to work but didn't" edge case."""

    @property
    def name(self) -> str:
        return "flaky"

    def is_available(self) -> bool:
        return True

    def generate(self, request: LLMRequest) -> LLMResponse:
        raise LLMUnavailableError(self.name)


# --- default (NullProvider) behavior: template fallback everywhere --------


def test_default_engine_uses_null_provider_and_falls_back_to_template() -> None:
    engine = ExplanationEngine(_main_engine())
    result = engine.explain(ExplanationRequest(node_id="backend"))
    assert result.generation_method == "template"
    assert result.provider_name is None


def test_node_explanation_end_to_end() -> None:
    """evidence -> fallback -> ExplanationResult, with no LLM available."""
    graph = _main_engine()
    engine = ExplanationEngine(graph)
    result = engine.explain(ExplanationRequest(node_id="backend"))
    expected = explain_node(build_node_evidence(graph, "backend"))
    assert result == expected


def test_relationship_explanation_end_to_end() -> None:
    graph = _main_engine()
    engine = ExplanationEngine(graph)
    result = engine.explain(ExplanationRequest(source_id="backend", target_id="db"))
    assert result.explanation == "backend depends on db."
    assert result.confidence == Confidence.HIGH
    assert result.generation_method == "template"
    assert result.provider_name is None


def test_graph_explanation_end_to_end() -> None:
    graph = _main_engine()
    engine = ExplanationEngine(graph)
    result = engine.explain_graph()
    assert "component(s)" in result.explanation
    assert result.generation_method == "template"


# --- every supported explanation operation, end-to-end ----------------------


def test_component_operation() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(node_id="backend"))
    assert "backend is a service component" in result.explanation


def test_dependencies_operation() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(node_id="backend"))
    assert "depends on: db" in result.explanation


def test_dependents_operation() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(node_id="db"))
    assert "depend on db" in result.explanation


def test_impact_operation() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(node_id="db"))
    assert "would be impacted if it changes: backend (directly); backend2 (transitively)." in result.explanation


def test_relationship_operation() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(source_id="backend2", target_id="db"))
    assert "indirect path" in result.explanation


def test_architecture_connections_operation() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(node_id="k8s-deploy"))
    assert "InfraLens infers" in result.explanation


def test_observations_operation() -> None:
    result = ExplanationEngine(_main_engine()).explain_graph()
    assert "Technologies detected" in result.explanation


def test_cycles_operation() -> None:
    result = ExplanationEngine(_cyclic_engine()).explain_graph()
    assert "dependency cycle" in result.explanation


# --- confidence banding, derived from evidence, not invented -----------------


def test_all_parsed_evidence_is_high_confidence() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(source_id="backend", target_id="db"))
    assert result.confidence == Confidence.HIGH


def test_parsed_mixed_with_high_confidence_inferred_is_mixed_confidence() -> None:
    result = ExplanationEngine(_mixed_confidence_engine()).explain(ExplanationRequest(node_id="hub"))
    assert result.confidence == Confidence.MIXED


def test_heuristic_inferred_evidence_is_low_confidence() -> None:
    result = ExplanationEngine(_main_engine()).explain(ExplanationRequest(node_id="k8s-deploy"))
    assert result.confidence == Confidence.LOW


# --- NodeNotFoundError propagation -------------------------------------------


def test_unknown_node_id_propagates_node_not_found_error() -> None:
    engine = ExplanationEngine(_main_engine())
    with pytest.raises(NodeNotFoundError):
        engine.explain(ExplanationRequest(node_id="does-not-exist"))


def test_unknown_relationship_source_propagates_node_not_found_error() -> None:
    engine = ExplanationEngine(_main_engine())
    with pytest.raises(NodeNotFoundError):
        engine.explain(ExplanationRequest(source_id="does-not-exist", target_id="db"))


# --- engine does not bypass the evidence/fallback layers --------------------


def test_engine_delegates_to_stage_5b_evidence_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _main_engine()
    calls = []

    import app.explanation.engine as engine_module

    original = engine_module.build_node_evidence

    def spy(graph_engine, node_id):
        calls.append((graph_engine, node_id))
        return original(graph_engine, node_id)

    monkeypatch.setattr(engine_module, "build_node_evidence", spy)

    ExplanationEngine(graph).explain(ExplanationRequest(node_id="backend"))

    assert calls == [(graph, "backend")]


def test_engine_delegates_to_stage_5c_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _main_engine()

    import app.explanation.engine as engine_module

    sentinel_calls = []
    original = engine_module.generate_fallback_explanation

    def spy(package):
        result = original(package)
        sentinel_calls.append(result)
        return result

    monkeypatch.setattr(engine_module, "generate_fallback_explanation", spy)

    result = ExplanationEngine(graph).explain(ExplanationRequest(node_id="backend"))

    assert len(sentinel_calls) == 1
    assert result == sentinel_calls[0]


def test_engine_never_computes_confidence_itself_even_on_llm_path() -> None:
    """Confidence on the LLM path must be exactly Stage 5C's own banding
    for the same evidence package, not something the engine derives."""
    graph = _main_engine()
    stub = _StubProvider()
    engine = ExplanationEngine(graph, provider=stub)

    result = engine.explain(ExplanationRequest(node_id="backend"))
    expected_confidence = explain_node(build_node_evidence(graph, "backend")).confidence

    assert result.confidence == expected_confidence


# --- LLM provider path (available / unavailable / flaky) --------------------


def test_available_provider_is_used_when_present() -> None:
    graph = _main_engine()
    stub = _StubProvider(text="a generated explanation", name="stub-provider")
    engine = ExplanationEngine(graph, provider=stub)

    result = engine.explain(ExplanationRequest(node_id="backend"))

    assert result.explanation == "a generated explanation"
    assert result.generation_method == "llm"
    assert result.provider_name == "stub-provider"
    assert stub.generate_calls == 1


def test_unavailable_provider_falls_back_to_template() -> None:
    graph = _main_engine()
    stub = _StubProvider(available=False)
    engine = ExplanationEngine(graph, provider=stub)

    result = engine.explain(ExplanationRequest(node_id="backend"))

    assert result.generation_method == "template"
    assert result.provider_name is None
    assert stub.generate_calls == 0


def test_provider_that_raises_llm_unavailable_error_falls_back_to_template() -> None:
    graph = _main_engine()
    engine = ExplanationEngine(graph, provider=_FlakyProvider())

    result = engine.explain(ExplanationRequest(node_id="backend"))

    assert result.generation_method == "template"
    assert result.provider_name is None
