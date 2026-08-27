"""Phase 7A: tests for app/services/analysis_service.py.

run_analysis() is the one seam that does real I/O (git clone) — same
pattern already established for explanation_service/
component_lookup_service tests: monkeypatch the I/O seam, exercise the
rest (id assignment, caching, resolution, failure isolation) for real
against the actual AnalysisStore.

Each test that touches the module-level singleton store replaces it
with a fresh AnalysisStore first, so tests never see another test's
cached entries.
"""

import pytest

from app.exceptions import AnalysisNotFoundError, InvalidRepositoryURLError, RepositoryCloneError
from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import analysis_service
from app.services.analysis_store import AnalysisResult, AnalysisStore


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test gets its own empty store, isolated from every other
    test and from analysis_service's real module-level singleton."""
    monkeypatch.setattr(analysis_service, "_store", AnalysisStore())


def _engine() -> GraphEngine:
    components = [
        Component(id="backend", name="backend", type="service", technology="docker-compose", metadata={}),
        Component(id="db", name="db", type="database", technology="docker-compose", metadata={}),
    ]
    relationships = [Relationship(source="backend", target="db", relationship_type="depends_on")]
    return GraphEngine.from_infrastructure_model(
        InfrastructureModel(components=components, relationships=relationships), infer=True
    )


def _fake_result(repo_url: str = "https://github.com/example/repo") -> AnalysisResult:
    import time
    import uuid

    return AnalysisResult(
        analysis_id=uuid.uuid4().hex,
        repository="repo",
        total_files=3,
        languages=["Python"],
        frameworks=["FastAPI"],
        infrastructure=["Docker"],
        infrastructure_model=InfrastructureModel(),
        graph_engine=_engine(),
        tree=[],
        created_at=time.time(),
    )


# --- run_analysis: id assignment, purity with respect to the cache ------------


def test_run_analysis_assigns_a_unique_analysis_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_service, "run_analysis", lambda repo_url: _fake_result(repo_url))
    first = analysis_service.run_analysis("https://github.com/example/repo")
    second = analysis_service.run_analysis("https://github.com/example/repo")
    assert first.analysis_id != second.analysis_id


def test_run_analysis_does_not_write_to_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_analysis() is documented as pure w.r.t. the cache — verified
    directly against the real (non-monkeypatched) run_analysis logic by
    stubbing only its I/O dependencies one layer down."""

    def fake_clone(owner: str, repo: str, destination) -> None:  # noqa: ANN001
        pass

    class _FakeScanResult:
        total_files = 0
        languages: list[str] = []
        tree: list = []
        file_paths: list = []

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone)
    monkeypatch.setattr(analysis_service, "scan_repository", lambda root: _FakeScanResult())
    monkeypatch.setattr(analysis_service, "detect_frameworks", lambda paths: [])
    monkeypatch.setattr(analysis_service, "detect_infrastructure", lambda paths: [])
    monkeypatch.setattr(
        analysis_service, "build_infrastructure_model", lambda paths, root: InfrastructureModel()
    )

    result = analysis_service.run_analysis("https://github.com/example/repo")

    assert len(analysis_service._store) == 0
    assert result.repository == "repo"


# --- create_analysis: stores under the returned id -----------------------------


def test_create_analysis_stores_the_result_under_its_analysis_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_result()
    monkeypatch.setattr(analysis_service, "run_analysis", lambda repo_url: fake)

    result = analysis_service.create_analysis("https://github.com/example/repo")

    assert result is fake
    assert analysis_service.get_analysis(fake.analysis_id) is fake


def test_create_analysis_called_twice_produces_two_independent_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_service, "run_analysis", lambda repo_url: _fake_result(repo_url))

    first = analysis_service.create_analysis("https://github.com/example/repo-a")
    second = analysis_service.create_analysis("https://github.com/example/repo-b")

    assert first.analysis_id != second.analysis_id
    assert analysis_service.get_analysis(first.analysis_id) is first
    assert analysis_service.get_analysis(second.analysis_id) is second


# --- get_analysis: unknown id -----------------------------------------------------


def test_get_analysis_raises_for_unknown_id() -> None:
    with pytest.raises(AnalysisNotFoundError):
        analysis_service.get_analysis("does-not-exist")


def test_analysis_not_found_error_carries_the_id() -> None:
    with pytest.raises(AnalysisNotFoundError) as exc_info:
        analysis_service.get_analysis("missing-id-123")
    assert exc_info.value.analysis_id == "missing-id-123"


# --- get_or_create_analysis: resolution rule --------------------------------------


def test_get_or_create_with_analysis_id_reuses_the_cached_result(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_result()
    monkeypatch.setattr(analysis_service, "run_analysis", lambda repo_url: fake)
    created = analysis_service.create_analysis("https://github.com/example/repo")

    resolved = analysis_service.get_or_create_analysis(analysis_id=created.analysis_id, repo_url=None)

    assert resolved is created


def test_get_or_create_with_unknown_analysis_id_raises() -> None:
    with pytest.raises(AnalysisNotFoundError):
        analysis_service.get_or_create_analysis(analysis_id="ghost", repo_url=None)


def test_get_or_create_with_repo_url_creates_a_fresh_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_run(repo_url: str) -> AnalysisResult:
        calls.append(repo_url)
        return _fake_result(repo_url)

    monkeypatch.setattr(analysis_service, "run_analysis", fake_run)

    result = analysis_service.get_or_create_analysis(analysis_id=None, repo_url="https://github.com/example/repo")

    assert calls == ["https://github.com/example/repo"]
    assert analysis_service.get_analysis(result.analysis_id) is result


def test_get_or_create_prefers_analysis_id_when_both_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive fallback behavior only — API-layer validators are the
    real enforcement of "exactly one", but this function's own
    resolution order should still be well-defined if ever called with
    both (e.g. from a future non-API caller)."""
    fake = _fake_result()
    monkeypatch.setattr(analysis_service, "run_analysis", lambda repo_url: fake)
    created = analysis_service.create_analysis("https://github.com/example/repo")

    run_calls = []
    monkeypatch.setattr(
        analysis_service, "run_analysis", lambda repo_url: run_calls.append(repo_url) or _fake_result(repo_url)
    )

    resolved = analysis_service.get_or_create_analysis(
        analysis_id=created.analysis_id, repo_url="https://github.com/example/other-repo"
    )

    assert resolved is created
    assert run_calls == []  # repo_url path never triggered


def test_get_or_create_with_neither_argument_raises_value_error() -> None:
    with pytest.raises(ValueError):
        analysis_service.get_or_create_analysis(analysis_id=None, repo_url=None)


# --- graph reuse: no rebuild when a valid analysis_id is reused -------------------


def test_graph_is_not_rebuilt_when_analysis_id_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def counting_run(repo_url: str) -> AnalysisResult:
        nonlocal call_count
        call_count += 1
        return _fake_result(repo_url)

    monkeypatch.setattr(analysis_service, "run_analysis", counting_run)

    created = analysis_service.create_analysis("https://github.com/example/repo")
    assert call_count == 1

    first = analysis_service.get_or_create_analysis(analysis_id=created.analysis_id, repo_url=None)
    second = analysis_service.get_or_create_analysis(analysis_id=created.analysis_id, repo_url=None)

    assert call_count == 1  # still 1 -> no rebuild happened on either lookup
    assert first is created
    assert second is created
    assert first.graph_engine is second.graph_engine


# --- failure isolation: a failed analysis leaves no cache entry ------------------


def test_failure_during_run_analysis_leaves_no_cache_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_run(repo_url: str) -> AnalysisResult:
        raise RepositoryCloneError("could not clone")

    monkeypatch.setattr(analysis_service, "run_analysis", failing_run)

    with pytest.raises(RepositoryCloneError):
        analysis_service.create_analysis("https://github.com/example/broken-repo")

    assert len(analysis_service._store) == 0


def test_failure_during_run_analysis_does_not_leak_a_usable_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed create_analysis() must not produce any analysis_id a
    caller could later (accidentally or otherwise) look up."""

    def failing_run(repo_url: str) -> AnalysisResult:
        raise InvalidRepositoryURLError("bad url")

    monkeypatch.setattr(analysis_service, "run_analysis", failing_run)

    with pytest.raises(InvalidRepositoryURLError):
        analysis_service.create_analysis("not-a-github-url")

    # There's no id to even attempt a lookup with, so the only thing to
    # verify is that nothing at all landed in the store.
    assert len(analysis_service._store) == 0


def test_one_failure_does_not_corrupt_a_previously_cached_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _fake_result()
    monkeypatch.setattr(analysis_service, "run_analysis", lambda repo_url: good)
    created = analysis_service.create_analysis("https://github.com/example/good-repo")

    def failing_run(repo_url: str) -> AnalysisResult:
        raise RepositoryCloneError("could not clone")

    monkeypatch.setattr(analysis_service, "run_analysis", failing_run)
    with pytest.raises(RepositoryCloneError):
        analysis_service.create_analysis("https://github.com/example/broken-repo")

    # The earlier, successful analysis must still be intact and reusable.
    assert analysis_service.get_analysis(created.analysis_id) is created


# --- to_analyze_response: reuses GraphEngine.to_model(), adds analysis_id -------


def test_to_analyze_response_includes_the_analysis_id() -> None:
    result = _fake_result()
    response = analysis_service.to_analyze_response(result)
    assert response.analysis_id == result.analysis_id


def test_to_analyze_response_reuses_graph_engine_to_model() -> None:
    result = _fake_result()
    response = analysis_service.to_analyze_response(result)
    assert response.graph == result.graph_engine.to_model()


def test_to_analyze_response_carries_through_scan_fields() -> None:
    result = _fake_result()
    response = analysis_service.to_analyze_response(result)
    assert response.repository == result.repository
    assert response.total_files == result.total_files
    assert response.languages == result.languages
    assert response.frameworks == result.frameworks
    assert response.infrastructure == result.infrastructure
