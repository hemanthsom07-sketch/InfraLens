"""Phase 7D: end-to-end integration tests for POST /graph/diagnostics and
POST /graph/path, in the same style as tests/test_impact_integration.py
and tests/test_dependency_integration.py — the real pipeline
(analysis_service.run_analysis -> ikm_service -> graph_service) against
real local repository fixtures, with only git_service.clone_repository
faked (copies a prepared tmp_repo into the destination instead of
running `git clone`).

Uses real ComposeParser ids ("compose:{relative_id}:{service_name}",
never the bare service name) via the same _service_id() helper
established in tests/test_analysis_session_integration.py,
tests/test_impact_integration.py, and tests/test_dependency_integration.py.

Two fixtures:

_acyclic fixture (fake_clone):
    backend -> db (depends_on), worker -> db (depends_on),
    frontend -> backend (depends_on), cache (isolated)
    Same dependency shape as the impact/dependency integration fixtures,
    but deliberately using plain `image:` references with no `build:`
    context and no Dockerfiles — see fake_clone's own docstring for why
    (app/graph/inference.py's Compose->Dockerfile USES-edge rule would
    otherwise add extra nodes into these exact-set assertions).

_cyclic fixture (fake_clone_cyclic):
    a -> b (depends_on), b -> a (depends_on) -- a genuine circular
    dependency. ComposeParser has no cycle-prevention logic (confirmed
    by inspection), so this parses into a real cycle in the graph,
    confirmed end-to-end here rather than only at the unit level.
"""

import shutil
from pathlib import Path

import pytest

from app.api.v1.analyze import analyze_repository
from app.api.v1.explain import ExplainAPIRequest, explain
from app.api.v1.graph_diagnostics import GraphDiagnosticsAPIRequest, GraphPathAPIRequest, get_graph_diagnostics, get_graph_path
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
    # Deliberately plain `image:` references, no `build:` context and no
    # Dockerfiles anywhere in this fixture. app/graph/inference.py's Rule
    # 2 (infer_compose_dockerfile_edges) adds a USES edge -- a dependency
    # edge type -- from a compose service to its resolved Dockerfile
    # whenever `build:` + a matching Dockerfile are both present. That
    # would add extra nodes into get_dependencies()/connected_components()
    # results this fixture doesn't intend to test, so it's avoided here
    # entirely rather than accounted for -- the exact-set assertions
    # below stay correct and unambiguous as a result.
    write(
        tmp_repo,
        "docker-compose.yml",
        """\
        services:
          db:
            image: postgres:16
          backend:
            image: myorg/backend:1.0
            depends_on:
              - db
          worker:
            image: myorg/worker:1.0
            depends_on:
              - db
          frontend:
            image: myorg/frontend:1.0
            depends_on:
              - backend
          cache:
            image: redis:7
        """,
    )

    def fake_clone_repository(owner: str, repo: str, destination: Path) -> None:
        shutil.copytree(tmp_repo, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)
    return tmp_repo


@pytest.fixture
def fake_clone_cyclic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    repo = tmp_path / "cyclic-repo"
    write(
        repo,
        "docker-compose.yml",
        """\
        services:
          a:
            image: a:1.0
            depends_on:
              - b
          b:
            image: b:1.0
            depends_on:
              - a
        """,
    )

    def fake_clone_repository(owner: str, repo_name: str, destination: Path) -> None:
        shutil.copytree(repo, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)
    return repo


# --- analyze -> analysis_id -> /graph/diagnostics (acyclic) --------------------


def test_analyze_then_diagnostics_on_acyclic_repo(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = GraphDiagnosticsAPIRequest(analysis_id=analyze_response.analysis_id)
    result = get_graph_diagnostics(request)

    assert result.cycles == []
    assert result.is_acyclic is True
    assert result.topological_order is not None
    order_ids = {n.id for n in result.topological_order}
    assert order_ids == {
        _service_id("db"), _service_id("backend"), _service_id("worker"), _service_id("frontend"), _service_id("cache")
    }


def test_analyze_then_diagnostics_connected_components(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = GraphDiagnosticsAPIRequest(analysis_id=analyze_response.analysis_id)
    result = get_graph_diagnostics(request)

    groups = [{n.id for n in group} for group in result.connected_components]
    assert {_service_id("cache")} in groups  # cache is isolated -- its own group
    main_group = next(g for g in groups if _service_id("db") in g)
    assert main_group == {
        _service_id("db"), _service_id("backend"), _service_id("worker"), _service_id("frontend")
    }


# --- analyze -> analysis_id -> /graph/diagnostics (cyclic) ---------------------


def test_analyze_then_diagnostics_on_cyclic_repo(fake_clone_cyclic: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/cyclic-repo"))

    request = GraphDiagnosticsAPIRequest(analysis_id=analyze_response.analysis_id)
    result = get_graph_diagnostics(request)

    assert result.is_acyclic is False
    assert result.topological_order is None
    assert len(result.cycles) == 1
    assert {n.id for n in result.cycles[0]} == {_service_id("a"), _service_id("b")}


# --- analyze -> analysis_id -> /graph/path --------------------------------------


def test_analyze_then_path_between_connected_nodes(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = GraphPathAPIRequest(
        source_id=_service_id("frontend"), target_id=_service_id("db"), analysis_id=analyze_response.analysis_id
    )
    result = get_graph_path(request)

    assert result.connected is True
    assert [n.id for n in result.path] == [_service_id("frontend"), _service_id("backend"), _service_id("db")]


def test_analyze_then_path_between_disconnected_nodes(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = GraphPathAPIRequest(
        source_id=_service_id("frontend"), target_id=_service_id("cache"), analysis_id=analyze_response.analysis_id
    )
    result = get_graph_path(request)

    assert result.connected is False
    assert result.path is None


# --- error paths: unknown node / unknown analysis_id ----------------------------


def test_path_unknown_source_raises(fake_clone: Path) -> None:
    from app.graph.exceptions import NodeNotFoundError

    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    request = GraphPathAPIRequest(
        source_id="does-not-exist", target_id=_service_id("db"), analysis_id=analyze_response.analysis_id
    )
    with pytest.raises(NodeNotFoundError):
        get_graph_path(request)


def test_path_unknown_target_raises(fake_clone: Path) -> None:
    from app.graph.exceptions import NodeNotFoundError

    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    request = GraphPathAPIRequest(
        source_id=_service_id("db"), target_id="does-not-exist", analysis_id=analyze_response.analysis_id
    )
    with pytest.raises(NodeNotFoundError):
        get_graph_path(request)


def test_diagnostics_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    request = GraphDiagnosticsAPIRequest(analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_graph_diagnostics(request)


def test_path_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    request = GraphPathAPIRequest(source_id="a", target_id="b", analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_graph_path(request)


# --- backward compatibility: repo_url path still works ---------------------------


def test_diagnostics_with_repo_url_still_works_without_an_analysis_id(fake_clone: Path) -> None:
    request = GraphDiagnosticsAPIRequest(repo_url="https://github.com/example/repo")
    result = get_graph_diagnostics(request)
    assert result.is_acyclic is True


def test_path_with_repo_url_still_works_without_an_analysis_id(fake_clone: Path) -> None:
    request = GraphPathAPIRequest(
        source_id=_service_id("frontend"), target_id=_service_id("db"), repo_url="https://github.com/example/repo"
    )
    result = get_graph_path(request)
    assert result.connected is True


# --- graph is not rebuilt when analysis_id is reused (end-to-end) --------------


def test_graph_not_rebuilt_across_diagnostics_path_and_explain_calls(
    fake_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    clone_calls = []
    original_clone = analysis_service.clone_repository

    def counting_clone(owner: str, repo: str, destination: Path) -> None:
        clone_calls.append((owner, repo))
        original_clone(owner, repo, destination)

    monkeypatch.setattr(analysis_service, "clone_repository", counting_clone)

    get_graph_diagnostics(GraphDiagnosticsAPIRequest(analysis_id=analyze_response.analysis_id))
    get_graph_path(
        GraphPathAPIRequest(
            source_id=_service_id("frontend"), target_id=_service_id("db"), analysis_id=analyze_response.analysis_id
        )
    )
    explain(ExplainAPIRequest(analysis_id=analyze_response.analysis_id, node_id=_service_id("backend")))

    assert clone_calls == []


# --- app wiring ------------------------------------------------------------------


def test_app_registers_graph_diagnostics_routes() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/graph/diagnostics" in paths
    assert "/api/v1/graph/path" in paths
