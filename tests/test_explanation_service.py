"""Stage 5E + Phase 7A: tests for app/services/explanation_service.py.

Phase 7A change: this module no longer clones directly (that seam,
_build_graph_engine, no longer exists here) — it resolves its
GraphEngine through app.services.analysis_service.get_or_create_analysis().
Tests that used to monkeypatch explanation_service._build_graph_engine
now monkeypatch analysis_service.get_or_create_analysis instead; every
assertion those original tests made is preserved, not weakened.
"""

import pytest

from app.explanation.engine import ExplanationEngine
from app.graph.engine import GraphEngine
from app.graph.exceptions import NodeNotFoundError
from app.models.explanation import ExplanationRequest
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import analysis_service, explanation_service
from tests.conftest import make_analysis_result


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
    ]
    relationships = [Relationship(source="backend", target="db", relationship_type="depends_on")]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


@pytest.fixture
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> GraphEngine:
    """Monkeypatch analysis_service.get_or_create_analysis() to skip the
    real clone/scan/build pipeline and resolve to a known, hand-built
    GraphEngine instead — the Phase 7A equivalent of the old
    _build_graph_engine seam."""
    graph = _main_engine()

    def fake_resolve(*, analysis_id, repo_url):
        return make_analysis_result(graph)

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    return graph


# --- repo_url path (unchanged behavior from before Phase 7A) -------------------


def test_explain_invokes_explanation_engine(patched_engine: GraphEngine) -> None:
    request = ExplanationRequest(node_id="backend")
    result = explanation_service.explain("https://github.com/example/repo", request)
    expected = ExplanationEngine(patched_engine).explain(request)
    assert result == expected


def test_explain_graph_invokes_explanation_engine(patched_engine: GraphEngine) -> None:
    result = explanation_service.explain_graph("https://github.com/example/repo")
    expected = ExplanationEngine(patched_engine).explain_graph()
    assert result == expected


def test_explain_passes_repo_url_through_to_analysis_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_main_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)

    explanation_service.explain("https://github.com/example/repo", ExplanationRequest(node_id="backend"))

    assert seen == [("https://github.com/example/repo", None)]


def test_explain_propagates_node_not_found_error(patched_engine: GraphEngine) -> None:
    request = ExplanationRequest(node_id="does-not-exist")
    with pytest.raises(NodeNotFoundError):
        explanation_service.explain("https://github.com/example/repo", request)


def test_explain_relationship_request(patched_engine: GraphEngine) -> None:
    request = ExplanationRequest(source_id="backend", target_id="db")
    result = explanation_service.explain("https://github.com/example/repo", request)
    assert result.explanation == "backend depends on db."


# --- analysis_id path (Phase 7A) ------------------------------------------------


def test_explain_with_analysis_id_passes_it_through_to_analysis_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_main_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)

    explanation_service.explain(
        None, ExplanationRequest(node_id="backend"), analysis_id="existing-analysis-id"
    )

    assert seen == [(None, "existing-analysis-id")]


def test_explain_with_analysis_id_returns_a_real_explanation(patched_engine: GraphEngine) -> None:
    request = ExplanationRequest(node_id="backend")
    result = explanation_service.explain(None, request, analysis_id="existing-analysis-id")
    expected = ExplanationEngine(patched_engine).explain(request)
    assert result == expected


def test_explain_graph_with_analysis_id_passes_it_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_main_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)

    explanation_service.explain_graph(None, analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


def test_explain_with_unknown_analysis_id_propagates_analysis_not_found_error() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        explanation_service.explain(None, ExplanationRequest(node_id="backend"), analysis_id="ghost-id")
