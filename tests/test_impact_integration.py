"""Phase 7B: end-to-end integration tests for POST /impact, in the same
style as tests/test_analysis_session_integration.py — the real pipeline
(analysis_service.run_analysis -> ikm_service -> graph_service) against
a real local repository fixture, with only git_service.clone_repository
faked (copies a prepared tmp_repo into the destination instead of
running `git clone`).

IMPORTANT (lesson learned from Phase 7A): ComposeParser ids each service
component "compose:{relative_id}:{service_name}", never the bare service
name — see app/parsers/compose_parser.py and app/parsers/base.py's
_relative_id(). Every id used below goes through the same _service_id()
helper test_analysis_session_integration.py already established, rather
than repeating a bare-name assumption.

Fixture graph (same shape as tests/test_impact_service.py's hand-built
one, but produced by the real Compose parser this time):

    backend  -> db        (backend depends_on db)
    worker   -> db         (worker depends_on db)
    frontend -> backend   (frontend depends_on backend)
    cache                  (isolated: no depends_on, nothing depends on it)
"""

import shutil
from pathlib import Path

import pytest

from app.api.v1.analyze import analyze_repository
from app.api.v1.explain import ExplainAPIRequest, explain
from app.api.v1.impact import ImpactAPIRequest, MultiNodeImpactResponse, get_impact
from app.models.graph import ImpactReport
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


# --- analyze -> analysis_id -> single-node impact -------------------------------


def test_analyze_then_impact_single_node_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = ImpactAPIRequest(node_id=_service_id("db"), analysis_id=analyze_response.analysis_id)
    result = get_impact(request)

    assert isinstance(result, ImpactReport)
    dependent_ids = {n.id for n in result.direct_dependents}
    assert dependent_ids == {_service_id("backend"), _service_id("worker")}
    assert {n.id for n in result.transitive_dependents} == {_service_id("frontend")}
    assert result.total_impact_count == 3


def test_analyze_then_impact_no_impact_node(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = ImpactAPIRequest(node_id=_service_id("cache"), analysis_id=analyze_response.analysis_id)
    result = get_impact(request)

    assert result.direct_dependents == []
    assert result.transitive_dependents == []
    assert result.total_impact_count == 0


# --- analyze -> analysis_id -> multi-node impact --------------------------------


def test_analyze_then_impact_multi_node_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = ImpactAPIRequest(
        node_ids=[_service_id("db"), _service_id("backend")], analysis_id=analyze_response.analysis_id
    )
    result = get_impact(request)

    assert isinstance(result, MultiNodeImpactResponse)
    direct_ids = {n.id for n in result.direct_dependents}
    transitive_ids = {n.id for n in result.transitive_dependents}

    # Same overlap case as test_impact_service.py's fixture: frontend is
    # transitive for db alone but direct for backend alone -> reclassified
    # direct in the combined result; backend (itself a target) legitimately
    # appears as a direct dependent of db.
    assert direct_ids == {_service_id("backend"), _service_id("worker"), _service_id("frontend")}
    assert transitive_ids == set()
    assert result.total_impact_count == 3
    assert set(result.per_node.keys()) == {_service_id("db"), _service_id("backend")}


# --- error paths: unknown node / unknown analysis_id ----------------------------


def test_impact_unknown_node_id_raises(fake_clone: Path) -> None:
    from app.graph.exceptions import NodeNotFoundError

    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    request = ImpactAPIRequest(node_id="does-not-exist", analysis_id=analyze_response.analysis_id)

    with pytest.raises(NodeNotFoundError):
        get_impact(request)


def test_impact_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    request = ImpactAPIRequest(node_id="anything", analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_impact(request)


# --- backward compatibility: repo_url path still works --------------------------


def test_impact_with_repo_url_still_works_without_an_analysis_id(fake_clone: Path) -> None:
    request = ImpactAPIRequest(node_id=_service_id("db"), repo_url="https://github.com/example/repo")
    result = get_impact(request)

    assert {n.id for n in result.direct_dependents} == {_service_id("backend"), _service_id("worker")}


# --- graph is not rebuilt when analysis_id is reused (end-to-end) --------------


def test_graph_not_rebuilt_across_impact_and_explain_calls_with_same_analysis_id(
    fake_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    clone_calls = []
    original_clone = analysis_service.clone_repository

    def counting_clone(owner: str, repo: str, destination: Path) -> None:
        clone_calls.append((owner, repo))
        original_clone(owner, repo, destination)

    monkeypatch.setattr(analysis_service, "clone_repository", counting_clone)

    # Reuse the same analysis_id across /impact (single-node), /impact
    # (multi-node), and /explain -- none of these should trigger another clone.
    get_impact(ImpactAPIRequest(node_id=_service_id("db"), analysis_id=analyze_response.analysis_id))
    get_impact(
        ImpactAPIRequest(
            node_ids=[_service_id("db"), _service_id("backend")], analysis_id=analyze_response.analysis_id
        )
    )
    explain(ExplainAPIRequest(analysis_id=analyze_response.analysis_id, node_id=_service_id("backend")))

    assert clone_calls == []


def test_impact_returns_results_from_the_identical_cached_graph(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    cached = analysis_service.get_analysis(analyze_response.analysis_id)

    request = ImpactAPIRequest(node_id=_service_id("db"), analysis_id=analyze_response.analysis_id)
    result = get_impact(request)

    # Same graph -> the impact result's target Node must be the literal
    # object GraphEngine.get_node() returns from the cached engine, not a
    # freshly rebuilt one.
    assert result.target == cached.graph_engine.get_node(_service_id("db"))


# --- app wiring ------------------------------------------------------------------


def test_app_registers_impact_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/impact" in paths
