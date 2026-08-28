"""Phase 7C: end-to-end integration tests for POST /dependencies, in the
same style as tests/test_impact_integration.py — the real pipeline
(analysis_service.run_analysis -> ikm_service -> graph_service) against
a real local repository fixture, with only git_service.clone_repository
faked (copies a prepared tmp_repo into the destination instead of
running `git clone`).

Uses real ComposeParser ids ("compose:{relative_id}:{service_name}",
never the bare service name) via the same _service_id() helper
established in tests/test_analysis_session_integration.py and
tests/test_impact_integration.py.

Fixture graph (identical shape to tests/test_impact_integration.py, so
the same overlap/transitivity reasoning applies here too):

    backend  -> db        (backend depends_on db)
    worker   -> db         (worker depends_on db)
    frontend -> backend   (frontend depends_on backend)
    cache                  (isolated: no depends_on, nothing depends on it)
"""

import shutil
from pathlib import Path

import pytest

from app.api.v1.analyze import analyze_repository
from app.api.v1.dependencies import DependencyAPIRequest, MultiNodeDependencyResponse, NodeDependencyResponse, get_dependencies
from app.api.v1.explain import ExplainAPIRequest, explain
from app.models.schemas import AnalyzeRequest
from app.services import analysis_service
from app.services.analysis_store import AnalysisStore
from tests.conftest import write

_COMPOSE_FILE = "docker-compose.yml"


def _service_id(service_name: str, *, compose_file: str = _COMPOSE_FILE) -> str:
    return f"compose:{compose_file}:{service_name}"


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_service, "_store", AnalysisStore())


@pytest.fixture
def fake_clone(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path) -> Path:
    write(
        tmp_repo,
        "docker-compose.yml",
        """\
        services:
          db:
            image: postgres:16
          backend:
            build: ./backend
            depends_on:
              - db
          worker:
            build: ./worker
            depends_on:
              - db
          frontend:
            build: ./frontend
            depends_on:
              - backend
          cache:
            image: redis:7
        """,
    )
    write(tmp_repo, "backend/Dockerfile", "FROM python:3.12-slim\n")
    write(tmp_repo, "worker/Dockerfile", "FROM python:3.12-slim\n")
    write(tmp_repo, "frontend/Dockerfile", "FROM node:20-slim\n")

    def fake_clone_repository(owner: str, repo: str, destination: Path) -> None:
        shutil.copytree(tmp_repo, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)
    return tmp_repo


# --- analyze -> analysis_id -> single-node dependencies -------------------------


def test_analyze_then_dependencies_single_node_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = DependencyAPIRequest(node_id=_service_id("frontend"), analysis_id=analyze_response.analysis_id)
    result = get_dependencies(request)

    assert isinstance(result, NodeDependencyResponse)
    # frontend depends_on backend, which depends_on db -> both are transitive dependencies
    dependency_ids = {n.id for n in result.dependencies}

    assert {_service_id("backend"), _service_id("db")} <= dependency_ids
    assert result.dependents == []


def test_analyze_then_dependencies_root_node_has_no_dependencies(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = DependencyAPIRequest(node_id=_service_id("db"), analysis_id=analyze_response.analysis_id)
    result = get_dependencies(request)

    assert result.dependencies == []
    assert {n.id for n in result.dependents} == {
        _service_id("backend"),
        _service_id("worker"),
        _service_id("frontend"),
    }


def test_analyze_then_dependencies_isolated_node_has_neither(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = DependencyAPIRequest(node_id=_service_id("cache"), analysis_id=analyze_response.analysis_id)
    result = get_dependencies(request)

    assert result.dependencies == []
    assert result.dependents == []


# --- analyze -> analysis_id -> multi-node dependencies ---------------------------


def test_analyze_then_dependencies_multi_node_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = DependencyAPIRequest(
        node_ids=[_service_id("backend"), _service_id("worker")], analysis_id=analyze_response.analysis_id
    )
    result = get_dependencies(request)

    assert isinstance(result, MultiNodeDependencyResponse)
    assert set(result.nodes.keys()) == {_service_id("backend"), _service_id("worker")}
    # both depend on db independently -- appears in both, not deduplicated/unioned away
    assert _service_id("db") in {n.id for n in result.nodes[_service_id("backend")].dependencies}
    assert _service_id("db") in {n.id for n in result.nodes[_service_id("worker")].dependencies}
    assert {n.id for n in result.nodes[_service_id("backend")].dependents} == {_service_id("frontend")}
    assert result.nodes[_service_id("worker")].dependents == []


# --- error paths: unknown node / unknown analysis_id ----------------------------


def test_dependencies_unknown_node_id_raises(fake_clone: Path) -> None:
    from app.graph.exceptions import NodeNotFoundError

    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    request = DependencyAPIRequest(node_id="does-not-exist", analysis_id=analyze_response.analysis_id)

    with pytest.raises(NodeNotFoundError):
        get_dependencies(request)


def test_dependencies_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    request = DependencyAPIRequest(node_id="anything", analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_dependencies(request)


# --- backward compatibility: repo_url path still works ---------------------------


def test_dependencies_with_repo_url_still_works_without_an_analysis_id(fake_clone: Path) -> None:
    request = DependencyAPIRequest(node_id=_service_id("backend"), repo_url="https://github.com/example/repo")
    result = get_dependencies(request)

    assert {n.id for n in result.dependencies} == {
    _service_id("db"),
    "docker:backend/Dockerfile",
}


# --- graph is not rebuilt when analysis_id is reused (end-to-end) --------------


def test_graph_not_rebuilt_across_dependencies_and_explain_calls_with_same_analysis_id(
    fake_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    clone_calls = []
    original_clone = analysis_service.clone_repository

    def counting_clone(owner: str, repo: str, destination: Path) -> None:
        clone_calls.append((owner, repo))
        original_clone(owner, repo, destination)

    monkeypatch.setattr(analysis_service, "clone_repository", counting_clone)

    # Reuse the same analysis_id across /dependencies (single-node),
    # /dependencies (multi-node), and /explain -- none of these should
    # trigger another clone.
    get_dependencies(DependencyAPIRequest(node_id=_service_id("backend"), analysis_id=analyze_response.analysis_id))
    get_dependencies(
        DependencyAPIRequest(
            node_ids=[_service_id("backend"), _service_id("worker")], analysis_id=analyze_response.analysis_id
        )
    )
    explain(ExplainAPIRequest(analysis_id=analyze_response.analysis_id, node_id=_service_id("backend")))

    assert clone_calls == []


def test_dependencies_returns_results_from_the_identical_cached_graph(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    cached = analysis_service.get_analysis(analyze_response.analysis_id)

    request = DependencyAPIRequest(node_id=_service_id("backend"), analysis_id=analyze_response.analysis_id)
    result = get_dependencies(request)

    expected_dependencies = cached.graph_engine.get_dependencies(_service_id("backend"))
    assert [n.id for n in result.dependencies] == [n.id for n in expected_dependencies]


# --- app wiring ------------------------------------------------------------------


def test_app_registers_dependencies_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/dependencies" in paths
