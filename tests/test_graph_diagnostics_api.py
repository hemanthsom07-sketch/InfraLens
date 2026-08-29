"""Phase 7D: tests for app/api/v1/graph_diagnostics.py.

No httpx / TestClient (same reasoning as the other API test files in
this suite): the route handlers are plain, synchronous Python functions,
called directly; request validation is exercised by constructing the
Pydantic request models directly.
"""

import pytest
from pydantic import ValidationError

from app.api.v1.graph_diagnostics import (
    GraphDiagnosticsAPIRequest,
    GraphDiagnosticsResponse,
    GraphPathAPIRequest,
    GraphPathResponse,
    get_graph_diagnostics,
    get_graph_path,
)
from app.graph.exceptions import NodeNotFoundError
from app.models.graph import Node
from app.services import graph_diagnostics_service
from app.services.graph_diagnostics_service import GraphDiagnosticsResult


def _node(id: str) -> Node:
    return Node(id=id, name=id, node_type="service", technology="test")


# --- /graph/diagnostics route: delegates, wraps into GraphDiagnosticsResponse --


def test_diagnostics_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = GraphDiagnosticsResult(
        cycles=[], topological_order=[_node("a"), _node("b")], connected_components=[[_node("a"), _node("b")]]
    )
    calls = []

    def fake_get_diagnostics(*, analysis_id=None, repo_url=None):
        calls.append((analysis_id, repo_url))
        return sentinel

    monkeypatch.setattr(graph_diagnostics_service, "get_graph_diagnostics", fake_get_diagnostics)

    request = GraphDiagnosticsAPIRequest(repo_url="https://github.com/example/repo")
    result = get_graph_diagnostics(request)

    assert calls == [(None, "https://github.com/example/repo")]
    assert isinstance(result, GraphDiagnosticsResponse)
    assert result.cycles == []
    assert [n.id for n in result.topological_order] == ["a", "b"]


def test_diagnostics_route_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = GraphDiagnosticsResult(cycles=[], topological_order=[], connected_components=[])
    calls = []

    def fake_get_diagnostics(*, analysis_id=None, repo_url=None):
        calls.append((analysis_id, repo_url))
        return sentinel

    monkeypatch.setattr(graph_diagnostics_service, "get_graph_diagnostics", fake_get_diagnostics)

    request = GraphDiagnosticsAPIRequest(analysis_id="existing-analysis-id")
    get_graph_diagnostics(request)

    assert calls == [("existing-analysis-id", None)]


def test_diagnostics_route_sets_is_acyclic_true_when_topological_order_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = GraphDiagnosticsResult(cycles=[], topological_order=[_node("a")], connected_components=[])
    monkeypatch.setattr(graph_diagnostics_service, "get_graph_diagnostics", lambda **kw: sentinel)

    request = GraphDiagnosticsAPIRequest(repo_url="https://github.com/example/repo")
    result = get_graph_diagnostics(request)

    assert result.is_acyclic is True


def test_diagnostics_route_sets_is_acyclic_false_when_topological_order_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = GraphDiagnosticsResult(cycles=[[_node("a"), _node("b")]], topological_order=None, connected_components=[])
    monkeypatch.setattr(graph_diagnostics_service, "get_graph_diagnostics", lambda **kw: sentinel)

    request = GraphDiagnosticsAPIRequest(repo_url="https://github.com/example/repo")
    result = get_graph_diagnostics(request)

    assert result.is_acyclic is False
    assert result.topological_order is None


def test_diagnostics_route_faithfully_passes_through_cycles_and_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = GraphDiagnosticsResult(
        cycles=[[_node("a"), _node("b"), _node("c")]],
        topological_order=None,
        connected_components=[[_node("a"), _node("b"), _node("c")]],
    )
    monkeypatch.setattr(graph_diagnostics_service, "get_graph_diagnostics", lambda **kw: sentinel)

    request = GraphDiagnosticsAPIRequest(repo_url="https://github.com/example/repo")
    result = get_graph_diagnostics(request)

    assert [n.id for n in result.cycles[0]] == ["a", "b", "c"]
    assert [n.id for n in result.connected_components[0]] == ["a", "b", "c"]


# --- /graph/diagnostics request validation --------------------------------------


def test_diagnostics_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    with pytest.raises(ValidationError):
        GraphDiagnosticsAPIRequest()


def test_diagnostics_request_rejects_both_repo_url_and_analysis_id() -> None:
    with pytest.raises(ValidationError):
        GraphDiagnosticsAPIRequest(repo_url="https://github.com/example/repo", analysis_id="some-id")


def test_diagnostics_request_accepts_repo_url_alone() -> None:
    request = GraphDiagnosticsAPIRequest(repo_url="https://github.com/example/repo")
    assert request.repo_url == "https://github.com/example/repo"


def test_diagnostics_request_accepts_analysis_id_alone() -> None:
    request = GraphDiagnosticsAPIRequest(analysis_id="existing-analysis-id")
    assert request.analysis_id == "existing-analysis-id"


# --- /graph/diagnostics error propagation ----------------------------------------


def test_diagnostics_route_propagates_analysis_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import AnalysisNotFoundError

    def fake_get_diagnostics(*, analysis_id=None, repo_url=None):
        raise AnalysisNotFoundError(analysis_id)

    monkeypatch.setattr(graph_diagnostics_service, "get_graph_diagnostics", fake_get_diagnostics)

    request = GraphDiagnosticsAPIRequest(analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_graph_diagnostics(request)


# --- /graph/path route: delegates, wraps into GraphPathResponse -----------------


def test_path_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get_path(source_id, target_id, *, analysis_id=None, repo_url=None):
        calls.append((source_id, target_id, analysis_id, repo_url))
        return [_node("a"), _node("b"), _node("c")]

    monkeypatch.setattr(graph_diagnostics_service, "get_shortest_path", fake_get_path)

    request = GraphPathAPIRequest(source_id="a", target_id="c", repo_url="https://github.com/example/repo")
    result = get_graph_path(request)

    assert calls == [("a", "c", None, "https://github.com/example/repo")]
    assert isinstance(result, GraphPathResponse)
    assert result.source_id == "a"
    assert result.target_id == "c"
    assert result.connected is True
    assert [n.id for n in result.path] == ["a", "b", "c"]


def test_path_route_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get_path(source_id, target_id, *, analysis_id=None, repo_url=None):
        calls.append((source_id, target_id, analysis_id, repo_url))
        return None

    monkeypatch.setattr(graph_diagnostics_service, "get_shortest_path", fake_get_path)

    request = GraphPathAPIRequest(source_id="a", target_id="z", analysis_id="existing-analysis-id")
    get_graph_path(request)

    assert calls == [("a", "z", "existing-analysis-id", None)]


def test_path_route_sets_connected_false_when_path_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_diagnostics_service, "get_shortest_path", lambda *a, **kw: None)

    request = GraphPathAPIRequest(source_id="a", target_id="iso", repo_url="https://github.com/example/repo")
    result = get_graph_path(request)

    assert result.connected is False
    assert result.path is None


def test_path_route_sets_connected_true_when_path_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_diagnostics_service, "get_shortest_path", lambda *a, **kw: [_node("a")])

    request = GraphPathAPIRequest(source_id="a", target_id="a", repo_url="https://github.com/example/repo")
    result = get_graph_path(request)

    assert result.connected is True


# --- /graph/path request validation -----------------------------------------------


def test_path_request_requires_source_and_target() -> None:
    with pytest.raises(ValidationError):
        GraphPathAPIRequest(source_id="a", repo_url="https://github.com/example/repo")
    with pytest.raises(ValidationError):
        GraphPathAPIRequest(target_id="c", repo_url="https://github.com/example/repo")


def test_path_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    with pytest.raises(ValidationError):
        GraphPathAPIRequest(source_id="a", target_id="c")


def test_path_request_rejects_both_repo_url_and_analysis_id() -> None:
    with pytest.raises(ValidationError):
        GraphPathAPIRequest(
            source_id="a", target_id="c", repo_url="https://github.com/example/repo", analysis_id="some-id"
        )


# --- /graph/path error propagation: unknown source/target/analysis_id -----------


def test_path_route_propagates_node_not_found_for_unknown_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_path(source_id, target_id, *, analysis_id=None, repo_url=None):
        raise NodeNotFoundError(source_id)

    monkeypatch.setattr(graph_diagnostics_service, "get_shortest_path", fake_get_path)

    request = GraphPathAPIRequest(source_id="does-not-exist", target_id="c", repo_url="https://github.com/example/repo")
    with pytest.raises(NodeNotFoundError):
        get_graph_path(request)


def test_path_route_propagates_node_not_found_for_unknown_target(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_path(source_id, target_id, *, analysis_id=None, repo_url=None):
        raise NodeNotFoundError(target_id)

    monkeypatch.setattr(graph_diagnostics_service, "get_shortest_path", fake_get_path)

    request = GraphPathAPIRequest(source_id="a", target_id="does-not-exist", repo_url="https://github.com/example/repo")
    with pytest.raises(NodeNotFoundError):
        get_graph_path(request)


def test_path_route_propagates_analysis_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import AnalysisNotFoundError

    def fake_get_path(source_id, target_id, *, analysis_id=None, repo_url=None):
        raise AnalysisNotFoundError(analysis_id)

    monkeypatch.setattr(graph_diagnostics_service, "get_shortest_path", fake_get_path)

    request = GraphPathAPIRequest(source_id="a", target_id="c", analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_graph_path(request)


# --- app wiring: routes registered, existing routes untouched -------------------


def test_app_registers_graph_diagnostics_routes() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/graph/diagnostics" in paths
    assert "/api/v1/graph/path" in paths


def test_app_still_registers_every_pre_existing_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/analyze" in paths
    assert "/api/v1/explain" in paths
    assert "/api/v1/explain/graph" in paths
    assert "/api/v1/components" in paths
    assert "/api/v1/impact" in paths
    assert "/api/v1/dependencies" in paths
