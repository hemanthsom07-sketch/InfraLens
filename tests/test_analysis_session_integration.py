"""Phase 7A: end-to-end integration tests across analysis_service, the
/analyze, /explain, and /components routes, and the AnalysisStore.

Unlike the other Phase 7A test files (which monkeypatch
analysis_service.get_or_create_analysis or .run_analysis directly to
isolate a single layer), these tests run the REAL pipeline
(analysis_service.run_analysis -> ikm_service -> graph_service) against
a real local repository fixture — only git_service.clone_repository is
faked, by copying a prepared tmp_repo into the destination instead of
actually invoking `git clone`. This is the closest these tests can get
to a genuine POST /analyze -> POST /explain call sequence without
network access, and is what actually exercises "the graph is not
rebuilt when analysis_id is reused" end-to-end rather than at a single
mocked layer.

Every test gets a fresh AnalysisStore (see _fresh_store below) so these
don't leak cached analyses into each other or into other test files.
"""

import shutil
from pathlib import Path

import pytest

from app.api.v1.analyze import analyze_repository
from app.api.v1.components import ComponentListRequest, list_components as list_components_route
from app.api.v1.explain import ExplainAPIRequest, ExplainGraphAPIRequest, explain, explain_graph
from app.models.schemas import AnalyzeRequest
from app.services import analysis_service
from app.services.analysis_store import AnalysisStore
from tests.conftest import write

# ComposeParser (app/parsers/compose_parser.py) ids each service
# component "compose:{relative_id}:{service_name}", not the bare
# service name — relative_id is the compose file's repo-root-relative
# posix path (app/parsers/base.py's _relative_id()). Every fixture
# below writes its compose file at the repo root as "docker-compose.yml",
# so that's the relative_id in every id built here. Spelled out as a
# helper, rather than repeating the literal string, so the tests read
# as "the real id for this service" instead of a magic string that
# happens to work.
_COMPOSE_FILE = "docker-compose.yml"


def _service_id(service_name: str, *, compose_file: str = _COMPOSE_FILE) -> str:
    return f"compose:{compose_file}:{service_name}"


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_service, "_store", AnalysisStore())


@pytest.fixture
def fake_clone(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path) -> Path:
    """Builds a small real docker-compose repository under `tmp_repo`,
    and makes analysis_service.run_analysis()'s clone step copy it into
    the (temporary) clone destination instead of running `git clone` —
    the one seam that needs faking to run the real pipeline offline."""
    write(
        tmp_repo,
        "docker-compose.yml",
        """\
        services:
          backend:
            build: ./backend
            depends_on:
              - db
          db:
            image: postgres:16
        """,
    )
    write(tmp_repo, "backend/Dockerfile", "FROM python:3.12-slim\n")

    def fake_clone_repository(owner: str, repo: str, destination: Path) -> None:
        shutil.copytree(tmp_repo, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)
    return tmp_repo


# --- analyze -> analysis_id -> explain -------------------------------------------


def test_analyze_then_explain_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    explain_request = ExplainAPIRequest(analysis_id=analyze_response.analysis_id, node_id=_service_id("backend"))
    result = explain(explain_request)

    assert "backend" in result.explanation


def test_analyze_then_explain_graph_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    result = explain_graph(ExplainGraphAPIRequest(analysis_id=analyze_response.analysis_id))

    assert result is not None


def test_analyze_then_explain_relationship_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    # backend depends_on db in the fixture compose file -> confirms the
    # real IKM/graph pipeline actually ran, not just an empty graph.
    request = ExplainAPIRequest(
        analysis_id=analyze_response.analysis_id, source_id=_service_id("backend"), target_id=_service_id("db")
    )
    result = explain(request)

    assert result.explanation


# --- analyze -> analysis_id -> components ----------------------------------------


def test_analyze_then_list_components_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = ComponentListRequest(analysis_id=analyze_response.analysis_id)
    response = list_components_route(request)

    ids = {c.id for c in response.components}
    assert _service_id("backend") in ids
    assert _service_id("db") in ids


def test_analyze_then_list_components_filters_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = ComponentListRequest(analysis_id=analyze_response.analysis_id, name_contains="back")
    response = list_components_route(request)

    assert {c.id for c in response.components} == {_service_id("backend")}


# --- graph is not rebuilt when analysis_id is reused (end-to-end) ---------------


def test_graph_not_rebuilt_across_multiple_analysis_id_calls(
    fake_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    clone_calls = []
    original_clone = analysis_service.clone_repository

    def counting_clone(owner: str, repo: str, destination: Path) -> None:
        clone_calls.append((owner, repo))
        original_clone(owner, repo, destination)

    monkeypatch.setattr(analysis_service, "clone_repository", counting_clone)

    # Reuse the same analysis_id across /explain, /explain/graph, and
    # /components — none of these should trigger another clone.
    explain(ExplainAPIRequest(analysis_id=analyze_response.analysis_id, node_id=_service_id("backend")))
    explain_graph(ExplainGraphAPIRequest(analysis_id=analyze_response.analysis_id))
    list_components_route(ComponentListRequest(analysis_id=analyze_response.analysis_id))

    assert clone_calls == []


def test_explain_with_analysis_id_returns_the_identical_graph_engine(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    cached = analysis_service.get_analysis(analyze_response.analysis_id)

    resolved_again = analysis_service.get_or_create_analysis(
        analysis_id=analyze_response.analysis_id, repo_url=None
    )

    assert resolved_again.graph_engine is cached.graph_engine


# --- backward compatibility: repo_url path still works end-to-end --------------


def test_explain_with_repo_url_still_triggers_a_fresh_clone(fake_clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone_calls = []
    original_clone = analysis_service.clone_repository

    def counting_clone(owner: str, repo: str, destination: Path) -> None:
        clone_calls.append((owner, repo))
        original_clone(owner, repo, destination)

    monkeypatch.setattr(analysis_service, "clone_repository", counting_clone)

    result = explain(ExplainAPIRequest(repo_url="https://github.com/example/repo", node_id=_service_id("backend")))

    assert len(clone_calls) == 1
    assert "backend" in result.explanation


def test_components_with_repo_url_still_works_without_an_analysis_id(fake_clone: Path) -> None:
    request = ComponentListRequest(repo_url="https://github.com/example/repo")
    response = list_components_route(request)

    ids = {c.id for c in response.components}
    assert _service_id("backend") in ids
    assert _service_id("db") in ids


def test_each_repo_url_call_creates_its_own_cached_analysis(fake_clone: Path) -> None:
    """A repo_url-based /explain call still creates (and caches) a
    fresh analysis under the hood, same as /analyze does — this
    verifies it doesn't silently reuse whatever was last cached."""
    first = explain(ExplainAPIRequest(repo_url="https://github.com/example/repo", node_id=_service_id("backend")))
    second = explain(ExplainAPIRequest(repo_url="https://github.com/example/repo", node_id=_service_id("backend")))

    assert first.explanation == second.explanation  # same repo -> same explanation
    assert len(analysis_service._store) == 2  # but two independent analyses were cached


# --- isolation: two different repos never cross-contaminate --------------------


def test_two_different_analyses_remain_isolated_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    write(repo_a, "docker-compose.yml", "services:\n  service-a:\n    image: a:1.0\n")
    write(repo_b, "docker-compose.yml", "services:\n  service-b:\n    image: b:1.0\n")

    def fake_clone_repository(owner: str, repo: str, destination: Path) -> None:
        source = repo_a if repo == "repo-a" else repo_b
        shutil.copytree(source, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)

    response_a = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo-a"))
    response_b = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo-b"))

    components_a = list_components_route(ComponentListRequest(analysis_id=response_a.analysis_id))
    components_b = list_components_route(ComponentListRequest(analysis_id=response_b.analysis_id))

    assert {c.id for c in components_a.components} == {_service_id("service-a")}
    assert {c.id for c in components_b.components} == {_service_id("service-b")}
