"""Phase 7C: tests for app/api/v1/dependencies.py.

No httpx / TestClient (same reasoning as the other API test files in
this suite): the route handler is a plain, synchronous Python function,
called directly; request validation is exercised by constructing the
Pydantic request model directly.
"""

import pytest
from pydantic import ValidationError

from app.api.v1.dependencies import DependencyAPIRequest, MultiNodeDependencyResponse, NodeDependencyResponse, get_dependencies
from app.graph.exceptions import NodeNotFoundError
from app.models.graph import Node
from app.services import dependency_service
from app.services.dependency_service import NodeDependencyResult


def _node(id: str) -> Node:
    return Node(id=id, name=id, node_type="service", technology="test")


# --- single-node route: delegates, wraps into NodeDependencyResponse ----------


def test_single_node_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = NodeDependencyResult(node_id="backend", dependencies=[_node("db")], dependents=[_node("frontend")])
    calls = []

    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        calls.append((node_id, analysis_id, repo_url))
        return sentinel

    monkeypatch.setattr(dependency_service, "get_single_node_dependencies", fake_get_single)

    request = DependencyAPIRequest(node_id="backend", repo_url="https://github.com/example/repo")
    result = get_dependencies(request)

    assert calls == [("backend", None, "https://github.com/example/repo")]
    assert isinstance(result, NodeDependencyResponse)
    assert result.node_id == "backend"
    assert [n.id for n in result.dependencies] == ["db"]
    assert [n.id for n in result.dependents] == ["frontend"]


def test_single_node_route_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = NodeDependencyResult(node_id="backend", dependencies=[], dependents=[])
    calls = []

    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        calls.append((node_id, analysis_id, repo_url))
        return sentinel

    monkeypatch.setattr(dependency_service, "get_single_node_dependencies", fake_get_single)

    request = DependencyAPIRequest(node_id="backend", analysis_id="existing-analysis-id")
    get_dependencies(request)

    assert calls == [("backend", "existing-analysis-id", None)]


def test_single_node_route_returns_empty_lists_faithfully(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = NodeDependencyResult(node_id="cache", dependencies=[], dependents=[])
    monkeypatch.setattr(dependency_service, "get_single_node_dependencies", lambda *a, **kw: sentinel)

    request = DependencyAPIRequest(node_id="cache", repo_url="https://github.com/example/repo")
    result = get_dependencies(request)

    assert result.dependencies == []
    assert result.dependents == []


# --- multi-node route: delegates, wraps into MultiNodeDependencyResponse ------


def test_multi_node_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    fake_results = {
        "backend": NodeDependencyResult(node_id="backend", dependencies=[_node("db")], dependents=[_node("frontend")]),
        "worker": NodeDependencyResult(node_id="worker", dependencies=[_node("db")], dependents=[]),
    }

    def fake_get_multi(node_ids, *, analysis_id=None, repo_url=None):
        calls.append((node_ids, analysis_id, repo_url))
        return fake_results

    monkeypatch.setattr(dependency_service, "get_multi_node_dependencies", fake_get_multi)

    request = DependencyAPIRequest(node_ids=["backend", "worker"], repo_url="https://github.com/example/repo")
    result = get_dependencies(request)

    assert calls == [(["backend", "worker"], None, "https://github.com/example/repo")]
    assert isinstance(result, MultiNodeDependencyResponse)
    assert set(result.nodes.keys()) == {"backend", "worker"}
    assert [n.id for n in result.nodes["backend"].dependencies] == ["db"]
    assert [n.id for n in result.nodes["worker"].dependents] == []


def test_multi_node_route_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    fake_results = {"backend": NodeDependencyResult(node_id="backend", dependencies=[], dependents=[])}

    def fake_get_multi(node_ids, *, analysis_id=None, repo_url=None):
        calls.append((node_ids, analysis_id, repo_url))
        return fake_results

    monkeypatch.setattr(dependency_service, "get_multi_node_dependencies", fake_get_multi)

    request = DependencyAPIRequest(node_ids=["backend"], analysis_id="existing-analysis-id")
    get_dependencies(request)

    assert calls == [(["backend"], "existing-analysis-id", None)]


def test_multi_node_route_does_not_combine_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each node's breakdown stays fully independent in the response --
    no union/dedup happens at the router layer either."""
    fake_results = {
        "backend": NodeDependencyResult(node_id="backend", dependencies=[_node("db")], dependents=[]),
        "worker": NodeDependencyResult(node_id="worker", dependencies=[_node("db")], dependents=[]),
    }
    monkeypatch.setattr(dependency_service, "get_multi_node_dependencies", lambda *a, **kw: fake_results)

    request = DependencyAPIRequest(node_ids=["backend", "worker"], repo_url="https://github.com/example/repo")
    result = get_dependencies(request)

    assert [n.id for n in result.nodes["backend"].dependencies] == ["db"]
    assert [n.id for n in result.nodes["worker"].dependencies] == ["db"]  # "db" appears in both, not deduplicated away


# --- request validation: node_id / node_ids shape ------------------------------


def test_request_rejects_neither_node_id_nor_node_ids() -> None:
    with pytest.raises(ValidationError):
        DependencyAPIRequest(repo_url="https://github.com/example/repo")


def test_request_rejects_both_node_id_and_node_ids() -> None:
    with pytest.raises(ValidationError):
        DependencyAPIRequest(node_id="backend", node_ids=["worker"], repo_url="https://github.com/example/repo")


def test_request_rejects_empty_node_ids_list() -> None:
    with pytest.raises(ValidationError):
        DependencyAPIRequest(node_ids=[], repo_url="https://github.com/example/repo")


def test_request_accepts_node_id_alone() -> None:
    request = DependencyAPIRequest(node_id="backend", repo_url="https://github.com/example/repo")
    assert request.node_id == "backend"
    assert request.node_ids is None


def test_request_accepts_node_ids_alone() -> None:
    request = DependencyAPIRequest(node_ids=["backend", "worker"], repo_url="https://github.com/example/repo")
    assert request.node_ids == ["backend", "worker"]
    assert request.node_id is None


# --- request validation: repo_url / analysis_id shape --------------------------


def test_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    with pytest.raises(ValidationError):
        DependencyAPIRequest(node_id="backend")


def test_request_rejects_both_repo_url_and_analysis_id() -> None:
    with pytest.raises(ValidationError):
        DependencyAPIRequest(node_id="backend", repo_url="https://github.com/example/repo", analysis_id="some-id")


def test_request_accepts_analysis_id_alone() -> None:
    request = DependencyAPIRequest(node_id="backend", analysis_id="existing-analysis-id")
    assert request.analysis_id == "existing-analysis-id"
    assert request.repo_url is None


def test_request_accepts_repo_url_alone() -> None:
    request = DependencyAPIRequest(node_id="backend", repo_url="https://github.com/example/repo")
    assert request.repo_url == "https://github.com/example/repo"
    assert request.analysis_id is None


# --- error propagation: unknown node / unknown analysis_id --------------------


def test_route_propagates_node_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        raise NodeNotFoundError(node_id)

    monkeypatch.setattr(dependency_service, "get_single_node_dependencies", fake_get_single)

    request = DependencyAPIRequest(node_id="does-not-exist", repo_url="https://github.com/example/repo")
    with pytest.raises(NodeNotFoundError):
        get_dependencies(request)


def test_route_propagates_analysis_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import AnalysisNotFoundError

    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        raise AnalysisNotFoundError(analysis_id)

    monkeypatch.setattr(dependency_service, "get_single_node_dependencies", fake_get_single)

    request = DependencyAPIRequest(node_id="backend", analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_dependencies(request)


def test_multi_node_route_propagates_node_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_multi(node_ids, *, analysis_id=None, repo_url=None):
        raise NodeNotFoundError(node_ids[-1])

    monkeypatch.setattr(dependency_service, "get_multi_node_dependencies", fake_get_multi)

    request = DependencyAPIRequest(
        node_ids=["backend", "does-not-exist"], repo_url="https://github.com/example/repo"
    )
    with pytest.raises(NodeNotFoundError):
        get_dependencies(request)


# --- app wiring: route registered, existing routes untouched ------------------


def test_app_registers_dependencies_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/dependencies" in paths


def test_app_still_registers_every_pre_existing_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/analyze" in paths
    assert "/api/v1/explain" in paths
    assert "/api/v1/explain/graph" in paths
    assert "/api/v1/components" in paths
    assert "/api/v1/impact" in paths
