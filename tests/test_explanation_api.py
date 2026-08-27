"""Stage 5E + Phase 7A: tests for app/api/v1/explain.py and app/main.py's
wiring.

No httpx / TestClient is used (the project deliberately has no httpx
dependency, and adding one just for tests is explicitly out of scope for
this stage). Route handlers here are plain, synchronous Python functions
(same as analyze_repository), so they're called directly like any other
function; request validation is exercised by constructing the Pydantic
request models directly, which is exactly what FastAPI itself does
before a handler ever runs.

Phase 7A change: ExplainAPIRequest/ExplainGraphAPIRequest now accept
`analysis_id` as an alternative to `repo_url`, and the routes pass
`analysis_id` through to explanation_service. Every original test's
delegation/validation assertion is preserved (fake service functions
now additionally accept the `analysis_id` keyword), not weakened.
"""

import asyncio

import pytest
from pydantic import ValidationError

from app.api.v1.explain import ExplainAPIRequest, ExplainGraphAPIRequest, explain, explain_graph
from app.graph.exceptions import NodeNotFoundError
from app.models.explanation import Confidence, ExplanationResult
from app.models.schemas import AnalyzeResponse
from app.services import explanation_service


def _sentinel_result() -> ExplanationResult:
    return ExplanationResult(
        explanation="sentinel",
        confidence=Confidence.HIGH,
        generation_method="template",
        provider_name=None,
    )


# --- /explain route: delegates to the service, nothing else -----------------


def test_explain_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_result()
    calls = []

    def fake_explain(repo_url, request, *, analysis_id=None):
        calls.append((repo_url, request, analysis_id))
        return sentinel

    monkeypatch.setattr(explanation_service, "explain", fake_explain)

    request = ExplainAPIRequest(repo_url="https://github.com/example/repo", node_id="backend")
    result = explain(request)

    assert result is sentinel
    assert calls == [("https://github.com/example/repo", request, None)]


def test_explain_graph_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_result()
    calls = []

    def fake_explain_graph(repo_url, *, analysis_id=None):
        calls.append((repo_url, analysis_id))
        return sentinel

    monkeypatch.setattr(explanation_service, "explain_graph", fake_explain_graph)

    request = ExplainGraphAPIRequest(repo_url="https://github.com/example/repo")
    result = explain_graph(request)

    assert result is sentinel
    assert calls == [("https://github.com/example/repo", None)]


def test_explain_route_delegates_analysis_id_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_result()
    calls = []

    def fake_explain(repo_url, request, *, analysis_id=None):
        calls.append((repo_url, request, analysis_id))
        return sentinel

    monkeypatch.setattr(explanation_service, "explain", fake_explain)

    request = ExplainAPIRequest(analysis_id="existing-analysis-id", node_id="backend")
    result = explain(request)

    assert result is sentinel
    assert calls == [(None, request, "existing-analysis-id")]


def test_explain_graph_route_delegates_analysis_id_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_result()
    calls = []

    def fake_explain_graph(repo_url, *, analysis_id=None):
        calls.append((repo_url, analysis_id))
        return sentinel

    monkeypatch.setattr(explanation_service, "explain_graph", fake_explain_graph)

    request = ExplainGraphAPIRequest(analysis_id="existing-analysis-id")
    result = explain_graph(request)

    assert result is sentinel
    assert calls == [(None, "existing-analysis-id")]


# --- request validation: neither node/pair input / both -> 422-equivalent ---


def test_explain_request_rejects_neither_node_nor_pair() -> None:
    with pytest.raises(ValidationError):
        ExplainAPIRequest(repo_url="https://github.com/example/repo")


def test_explain_request_rejects_node_and_pair_together() -> None:
    with pytest.raises(ValidationError):
        ExplainAPIRequest(
            repo_url="https://github.com/example/repo",
            node_id="backend",
            source_id="backend",
            target_id="db",
        )


def test_explain_request_rejects_source_without_target() -> None:
    with pytest.raises(ValidationError):
        ExplainAPIRequest(repo_url="https://github.com/example/repo", source_id="backend")


def test_explain_request_accepts_node_id_alone() -> None:
    request = ExplainAPIRequest(repo_url="https://github.com/example/repo", node_id="backend")
    assert request.node_id == "backend"


def test_explain_request_accepts_source_and_target_together() -> None:
    request = ExplainAPIRequest(repo_url="https://github.com/example/repo", source_id="backend", target_id="db")
    assert request.source_id == "backend"
    assert request.target_id == "db"


def test_explain_graph_request_only_needs_repo_url() -> None:
    request = ExplainGraphAPIRequest(repo_url="https://github.com/example/repo")
    assert request.repo_url == "https://github.com/example/repo"


# --- request validation: repo_url / analysis_id shape (Phase 7A) -----------


def test_explain_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    with pytest.raises(ValidationError):
        ExplainAPIRequest(node_id="backend")


def test_explain_request_rejects_both_repo_url_and_analysis_id() -> None:
    with pytest.raises(ValidationError):
        ExplainAPIRequest(
            repo_url="https://github.com/example/repo", analysis_id="some-id", node_id="backend"
        )


def test_explain_request_accepts_analysis_id_alone() -> None:
    request = ExplainAPIRequest(analysis_id="existing-analysis-id", node_id="backend")
    assert request.analysis_id == "existing-analysis-id"
    assert request.repo_url is None


def test_explain_graph_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    with pytest.raises(ValidationError):
        ExplainGraphAPIRequest()


def test_explain_graph_request_rejects_both_repo_url_and_analysis_id() -> None:
    with pytest.raises(ValidationError):
        ExplainGraphAPIRequest(repo_url="https://github.com/example/repo", analysis_id="some-id")


def test_explain_graph_request_accepts_analysis_id_alone() -> None:
    request = ExplainGraphAPIRequest(analysis_id="existing-analysis-id")
    assert request.analysis_id == "existing-analysis-id"
    assert request.repo_url is None


# --- unknown node -> NodeNotFoundError propagates to the app's handler -----


def test_explain_route_propagates_node_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_explain(repo_url, request, *, analysis_id=None):
        raise NodeNotFoundError(request.node_id)

    monkeypatch.setattr(explanation_service, "explain", fake_explain)

    request = ExplainAPIRequest(repo_url="https://github.com/example/repo", node_id="does-not-exist")
    with pytest.raises(NodeNotFoundError):
        explain(request)


def test_node_not_found_handler_returns_404() -> None:
    from app.main import node_not_found_handler

    response = asyncio.run(node_not_found_handler(None, NodeNotFoundError("does-not-exist")))

    assert response.status_code == 404
    assert b"does-not-exist" in response.body


# --- unknown/expired analysis_id -> AnalysisNotFoundError propagates (Phase 7A) --


def test_explain_route_propagates_analysis_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import AnalysisNotFoundError

    def fake_explain(repo_url, request, *, analysis_id=None):
        raise AnalysisNotFoundError(analysis_id)

    monkeypatch.setattr(explanation_service, "explain", fake_explain)

    request = ExplainAPIRequest(analysis_id="ghost-id", node_id="backend")
    with pytest.raises(AnalysisNotFoundError):
        explain(request)


def test_analysis_not_found_handler_returns_404() -> None:
    from app.exceptions import AnalysisNotFoundError
    from app.main import analysis_not_found_handler

    response = asyncio.run(analysis_not_found_handler(None, AnalysisNotFoundError("ghost-id")))

    assert response.status_code == 404
    assert b"ghost-id" in response.body


# --- app wiring: new routes registered, existing /analyze untouched --------


def test_app_registers_explain_routes() -> None:
    from app.main import app

    # app.routes' element type isn't stable across FastAPI/Starlette
    # versions (some wrap included sub-router routes in an internal
    # object without a `.path` attribute). app.openapi()["paths"] is
    # FastAPI's own public, documented view of every registered route —
    # robust regardless of the internal Route representation.
    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/explain" in paths
    assert "/api/v1/explain/graph" in paths


def test_app_still_registers_analyze_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/analyze" in paths


def test_analyze_response_schema_includes_analysis_id() -> None:
    """Phase 7A: AnalyzeResponse gains analysis_id — a deliberate,
    additive schema change, not a regression. Every field the schema
    had before Phase 7A is still present unchanged; this test's own
    name/intent changed from "unchanged" to "includes analysis_id" to
    reflect that."""
    assert set(AnalyzeResponse.model_fields.keys()) == {
        "analysis_id",
        "repository",
        "total_files",
        "languages",
        "frameworks",
        "infrastructure",
        "infrastructure_model",
        "graph",
        "tree",
    }
