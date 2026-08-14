"""Stage 5E: tests for app/api/v1/explain.py and app/main.py's wiring.

No httpx / TestClient is used (the project deliberately has no httpx
dependency, and adding one just for tests is explicitly out of scope for
this stage). Route handlers here are plain, synchronous Python functions
(same as analyze_repository), so they're called directly like any other
function; request validation is exercised by constructing the Pydantic
request models directly, which is exactly what FastAPI itself does
before a handler ever runs.
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

    def fake_explain(repo_url, request):
        calls.append((repo_url, request))
        return sentinel

    monkeypatch.setattr(explanation_service, "explain", fake_explain)

    request = ExplainAPIRequest(repo_url="https://github.com/example/repo", node_id="backend")
    result = explain(request)

    assert result is sentinel
    assert calls == [("https://github.com/example/repo", request)]


def test_explain_graph_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_result()
    calls = []

    def fake_explain_graph(repo_url):
        calls.append(repo_url)
        return sentinel

    monkeypatch.setattr(explanation_service, "explain_graph", fake_explain_graph)

    request = ExplainGraphAPIRequest(repo_url="https://github.com/example/repo")
    result = explain_graph(request)

    assert result is sentinel
    assert calls == ["https://github.com/example/repo"]


# --- request validation: neither input / both inputs -> 422-equivalent ------


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


# --- unknown node -> NodeNotFoundError propagates to the app's handler -----


def test_explain_route_propagates_node_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_explain(repo_url, request):
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


def test_analyze_response_schema_unchanged() -> None:
    assert set(AnalyzeResponse.model_fields.keys()) == {
        "repository",
        "total_files",
        "languages",
        "frameworks",
        "infrastructure",
        "infrastructure_model",
        "graph",
        "tree",
    }
