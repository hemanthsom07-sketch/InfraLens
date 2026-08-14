"""Stage 5E: tests for app/services/explanation_service.py.

_build_graph_engine() does a real `git clone`, which this sandbox can't
do (no network) and which existing tests avoid too — test_graph_pipeline.py
etc. build graphs from local tmp_repo fixtures rather than cloning from
GitHub. Here, we monkeypatch _build_graph_engine() itself (the one seam
that does I/O) so the rest of the service — the actual thing Stage 5E
adds — is exercised against a real, hand-built GraphEngine.
"""

import pytest

from app.explanation.engine import ExplanationEngine
from app.graph.engine import GraphEngine
from app.graph.exceptions import NodeNotFoundError
from app.models.explanation import ExplanationRequest
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import explanation_service


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
    """Monkeypatch explanation_service._build_graph_engine() to skip the
    real clone and return a known, hand-built GraphEngine instead."""
    graph = _main_engine()

    def fake_build(repo_url: str) -> GraphEngine:
        return graph

    monkeypatch.setattr(explanation_service, "_build_graph_engine", fake_build)
    return graph


def test_explain_invokes_explanation_engine(patched_engine: GraphEngine) -> None:
    request = ExplanationRequest(node_id="backend")
    result = explanation_service.explain("https://github.com/example/repo", request)
    expected = ExplanationEngine(patched_engine).explain(request)
    assert result == expected


def test_explain_graph_invokes_explanation_engine(patched_engine: GraphEngine) -> None:
    result = explanation_service.explain_graph("https://github.com/example/repo")
    expected = ExplanationEngine(patched_engine).explain_graph()
    assert result == expected


def test_explain_passes_repo_url_through_to_build_graph_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_build(repo_url: str) -> GraphEngine:
        seen.append(repo_url)
        return _main_engine()

    monkeypatch.setattr(explanation_service, "_build_graph_engine", fake_build)

    explanation_service.explain("https://github.com/example/repo", ExplanationRequest(node_id="backend"))

    assert seen == ["https://github.com/example/repo"]


def test_explain_propagates_node_not_found_error(patched_engine: GraphEngine) -> None:
    request = ExplanationRequest(node_id="does-not-exist")
    with pytest.raises(NodeNotFoundError):
        explanation_service.explain("https://github.com/example/repo", request)


def test_explain_relationship_request(patched_engine: GraphEngine) -> None:
    request = ExplanationRequest(source_id="backend", target_id="db")
    result = explanation_service.explain("https://github.com/example/repo", request)
    assert result.explanation == "backend depends on db."
