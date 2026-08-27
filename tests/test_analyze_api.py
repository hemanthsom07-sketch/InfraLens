"""Phase 7A: tests for app/api/v1/analyze.py.

No httpx / TestClient (same reasoning as test_explanation_api.py): the
route handler is a plain, synchronous Python function, called directly.
There was no dedicated test file for this route before Phase 7A (it had
no test-relevant branching of its own — it just wired
git_service/scanner_service/ikm_service/graph_service together inline).
Phase 7A gives it real logic worth testing directly: delegating to
analysis_service and returning analysis_id.
"""

import pytest

from app.api.v1.analyze import analyze_repository
from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.services import analysis_service


def _fake_response(analysis_id: str = "fake-analysis-id") -> AnalyzeResponse:
    from app.models.graph import GraphModel
    from app.models.ikm import InfrastructureModel

    return AnalyzeResponse(
        analysis_id=analysis_id,
        repository="repo",
        total_files=3,
        languages=["Python"],
        frameworks=["FastAPI"],
        infrastructure=["Docker"],
        infrastructure_model=InfrastructureModel(),
        graph=GraphModel(nodes=[], edges=[]),
        tree=[],
    )


def test_analyze_route_delegates_to_analysis_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class _FakeResult:
        pass

    sentinel_result = _FakeResult()

    def fake_create_analysis(repo_url: str):
        calls.append(("create", repo_url))
        return sentinel_result

    def fake_to_analyze_response(result):
        calls.append(("to_response", result))
        return _fake_response()

    monkeypatch.setattr(analysis_service, "create_analysis", fake_create_analysis)
    monkeypatch.setattr(analysis_service, "to_analyze_response", fake_to_analyze_response)

    request = AnalyzeRequest(repo_url="https://github.com/example/repo")
    response = analyze_repository(request)

    assert calls == [("create", "https://github.com/example/repo"), ("to_response", sentinel_result)]
    assert response.analysis_id == "fake-analysis-id"


def test_analyze_route_returns_an_analysis_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_service, "create_analysis", lambda repo_url: object())
    monkeypatch.setattr(analysis_service, "to_analyze_response", lambda result: _fake_response("abc-123"))

    request = AnalyzeRequest(repo_url="https://github.com/example/repo")
    response = analyze_repository(request)

    assert response.analysis_id == "abc-123"


def test_analyze_route_propagates_invalid_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import InvalidRepositoryURLError

    def failing_create(repo_url: str):
        raise InvalidRepositoryURLError("bad url")

    monkeypatch.setattr(analysis_service, "create_analysis", failing_create)

    request = AnalyzeRequest(repo_url="not-a-github-url")
    with pytest.raises(InvalidRepositoryURLError):
        analyze_repository(request)


def test_analyze_request_schema_unchanged() -> None:
    """Backward compatibility (Phase 7A spec item 5): AnalyzeRequest
    still only takes repo_url — /analyze always creates a fresh
    analysis; it doesn't accept an analysis_id to reuse (that's what
    /explain and /components are for)."""
    assert set(AnalyzeRequest.model_fields.keys()) == {"repo_url"}


def test_app_registers_analyze_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/analyze" in paths
