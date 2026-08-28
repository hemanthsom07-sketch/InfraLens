"""Phase 7B: tests for app/api/v1/impact.py.

No httpx / TestClient (same reasoning as the other API test files in
this suite): the route handler is a plain, synchronous Python function,
called directly; request validation is exercised by constructing the
Pydantic request model directly, exactly what FastAPI itself does before
a handler ever runs.
"""

import pytest
from pydantic import ValidationError

from app.api.v1.impact import ImpactAPIRequest, MultiNodeImpactResponse, get_impact
from app.graph.exceptions import NodeNotFoundError
from app.models.graph import ImpactReport, Node
from app.services import impact_service


def _node(id: str) -> Node:
    return Node(id=id, name=id, node_type="service", technology="test")


def _sentinel_report() -> ImpactReport:
    return ImpactReport(
        target=_node("db"),
        direct_dependents=[_node("backend")],
        transitive_dependents=[],
        total_impact_count=1,
        impact_by_type={"service": 1},
    )


# --- single-node route: delegates, returns ImpactReport unmodified -------------


def test_single_node_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_report()
    calls = []

    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        calls.append((node_id, analysis_id, repo_url))
        return sentinel

    monkeypatch.setattr(impact_service, "get_single_node_impact", fake_get_single)

    request = ImpactAPIRequest(node_id="db", repo_url="https://github.com/example/repo")
    result = get_impact(request)

    assert result is sentinel
    assert calls == [("db", None, "https://github.com/example/repo")]


def test_single_node_route_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_report()
    calls = []

    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        calls.append((node_id, analysis_id, repo_url))
        return sentinel

    monkeypatch.setattr(impact_service, "get_single_node_impact", fake_get_single)

    request = ImpactAPIRequest(node_id="db", analysis_id="existing-analysis-id")
    get_impact(request)

    assert calls == [("db", "existing-analysis-id", None)]


def test_single_node_route_returns_unmodified_impact_report(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = _sentinel_report()
    monkeypatch.setattr(impact_service, "get_single_node_impact", lambda *a, **kw: sentinel)

    request = ImpactAPIRequest(node_id="db", repo_url="https://github.com/example/repo")
    result = get_impact(request)

    assert isinstance(result, ImpactReport)
    assert result is sentinel  # not reshaped, not copied, not wrapped


# --- multi-node route: delegates, wraps into MultiNodeImpactResponse ----------


def test_multi_node_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class _FakeResult:
        targets = [_node("db"), _node("backend")]
        direct_dependents = [_node("frontend")]
        transitive_dependents = []
        total_impact_count = 1
        impact_by_type = {"service": 1}
        per_node = {"db": _sentinel_report(), "backend": _sentinel_report()}

    def fake_get_multi(node_ids, *, analysis_id=None, repo_url=None):
        calls.append((node_ids, analysis_id, repo_url))
        return _FakeResult()

    monkeypatch.setattr(impact_service, "get_multi_node_impact", fake_get_multi)

    request = ImpactAPIRequest(node_ids=["db", "backend"], repo_url="https://github.com/example/repo")
    result = get_impact(request)

    assert calls == [(["db", "backend"], None, "https://github.com/example/repo")]
    assert isinstance(result, MultiNodeImpactResponse)
    assert [t.id for t in result.targets] == ["db", "backend"]
    assert result.total_impact_count == 1


def test_multi_node_route_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class _FakeResult:
        targets = [_node("db")]
        direct_dependents = []
        transitive_dependents = []
        total_impact_count = 0
        impact_by_type = {}
        per_node = {"db": _sentinel_report()}

    def fake_get_multi(node_ids, *, analysis_id=None, repo_url=None):
        calls.append((node_ids, analysis_id, repo_url))
        return _FakeResult()

    monkeypatch.setattr(impact_service, "get_multi_node_impact", fake_get_multi)

    request = ImpactAPIRequest(node_ids=["db"], analysis_id="existing-analysis-id")
    get_impact(request)

    assert calls == [(["db"], "existing-analysis-id", None)]


def test_multi_node_route_preserves_per_node_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    per_node_reports = {"db": _sentinel_report(), "backend": _sentinel_report()}

    class _FakeResult:
        targets = [_node("db"), _node("backend")]
        direct_dependents = []
        transitive_dependents = []
        total_impact_count = 0
        impact_by_type = {}
        per_node = per_node_reports

    monkeypatch.setattr(impact_service, "get_multi_node_impact", lambda *a, **kw: _FakeResult())

    request = ImpactAPIRequest(node_ids=["db", "backend"], repo_url="https://github.com/example/repo")
    result = get_impact(request)

    assert set(result.per_node.keys()) == {"db", "backend"}


# --- request validation: node_id / node_ids shape ------------------------------


def test_request_rejects_neither_node_id_nor_node_ids() -> None:
    with pytest.raises(ValidationError):
        ImpactAPIRequest(repo_url="https://github.com/example/repo")


def test_request_rejects_both_node_id_and_node_ids() -> None:
    with pytest.raises(ValidationError):
        ImpactAPIRequest(
            node_id="db", node_ids=["backend"], repo_url="https://github.com/example/repo"
        )


def test_request_rejects_empty_node_ids_list() -> None:
    with pytest.raises(ValidationError):
        ImpactAPIRequest(node_ids=[], repo_url="https://github.com/example/repo")


def test_request_accepts_node_id_alone() -> None:
    request = ImpactAPIRequest(node_id="db", repo_url="https://github.com/example/repo")
    assert request.node_id == "db"
    assert request.node_ids is None


def test_request_accepts_node_ids_alone() -> None:
    request = ImpactAPIRequest(node_ids=["db", "backend"], repo_url="https://github.com/example/repo")
    assert request.node_ids == ["db", "backend"]
    assert request.node_id is None


# --- request validation: repo_url / analysis_id shape (mirrors explain.py) ----


def test_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    with pytest.raises(ValidationError):
        ImpactAPIRequest(node_id="db")


def test_request_rejects_both_repo_url_and_analysis_id() -> None:
    with pytest.raises(ValidationError):
        ImpactAPIRequest(node_id="db", repo_url="https://github.com/example/repo", analysis_id="some-id")


def test_request_accepts_analysis_id_alone() -> None:
    request = ImpactAPIRequest(node_id="db", analysis_id="existing-analysis-id")
    assert request.analysis_id == "existing-analysis-id"
    assert request.repo_url is None


def test_request_accepts_repo_url_alone() -> None:
    request = ImpactAPIRequest(node_id="db", repo_url="https://github.com/example/repo")
    assert request.repo_url == "https://github.com/example/repo"
    assert request.analysis_id is None


# --- error propagation: unknown node / unknown analysis_id --------------------


def test_route_propagates_node_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        raise NodeNotFoundError(node_id)

    monkeypatch.setattr(impact_service, "get_single_node_impact", fake_get_single)

    request = ImpactAPIRequest(node_id="does-not-exist", repo_url="https://github.com/example/repo")
    with pytest.raises(NodeNotFoundError):
        get_impact(request)


def test_route_propagates_analysis_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import AnalysisNotFoundError

    def fake_get_single(node_id, *, analysis_id=None, repo_url=None):
        raise AnalysisNotFoundError(analysis_id)

    monkeypatch.setattr(impact_service, "get_single_node_impact", fake_get_single)

    request = ImpactAPIRequest(node_id="db", analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_impact(request)


def test_multi_node_route_propagates_node_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_multi(node_ids, *, analysis_id=None, repo_url=None):
        raise NodeNotFoundError(node_ids[-1])

    monkeypatch.setattr(impact_service, "get_multi_node_impact", fake_get_multi)

    request = ImpactAPIRequest(node_ids=["db", "does-not-exist"], repo_url="https://github.com/example/repo")
    with pytest.raises(NodeNotFoundError):
        get_impact(request)


# --- app wiring: route registered, existing routes untouched ------------------


def test_app_registers_impact_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/impact" in paths


def test_app_still_registers_every_pre_existing_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/analyze" in paths
    assert "/api/v1/explain" in paths
    assert "/api/v1/explain/graph" in paths
    assert "/api/v1/components" in paths
