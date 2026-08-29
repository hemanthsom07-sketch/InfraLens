"""Phase 7E: tests for app/api/v1/security.py.

No httpx / TestClient (same reasoning as the other API test files in
this suite): the route handler is a plain, synchronous Python function,
called directly.
"""

import pytest
from pydantic import ValidationError

from app.api.v1.security import SecurityAPIRequest, SecurityResponse, get_security_findings
from app.explanation.evidence import Observation, ObservationKind
from app.services import security_service


def _finding_observation(
    rule_id: str = "MUTABLE_IMAGE_TAG",
    severity: str = "medium",
    component_id: str = "compose:x:app",
) -> Observation:
    return Observation(
        kind=ObservationKind.SECURITY_FINDING,
        subject_id=component_id,
        detail={
            "rule_id": rule_id,
            "severity": severity,
            "title": "Mutable or missing image tag",
            "reason": "Service image 'myapp:latest' has no tag, or is tagged ':latest'.",
            "component_name": "app",
            "technology": "docker-compose",
            "source_file": "docker-compose.yml",
        },
        weight=0.6,
    )


# --- route: delegates, maps Observations into SecurityFinding/SecurityResponse -


def test_route_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get_findings(*, analysis_id=None, repo_url=None):
        calls.append((analysis_id, repo_url))
        return [_finding_observation()]

    monkeypatch.setattr(security_service, "get_security_findings", fake_get_findings)

    request = SecurityAPIRequest(repo_url="https://github.com/example/repo")
    result = get_security_findings(request)

    assert calls == [(None, "https://github.com/example/repo")]
    assert isinstance(result, SecurityResponse)
    assert result.total_count == 1


def test_route_passes_analysis_id_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get_findings(*, analysis_id=None, repo_url=None):
        calls.append((analysis_id, repo_url))
        return []

    monkeypatch.setattr(security_service, "get_security_findings", fake_get_findings)

    request = SecurityAPIRequest(analysis_id="existing-analysis-id")
    get_security_findings(request)

    assert calls == [("existing-analysis-id", None)]


def test_route_maps_observation_fields_to_security_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security_service, "get_security_findings", lambda **kw: [_finding_observation()])

    request = SecurityAPIRequest(repo_url="https://github.com/example/repo")
    result = get_security_findings(request)

    finding = result.findings[0]
    assert finding.rule_id == "MUTABLE_IMAGE_TAG"
    assert finding.severity == "medium"
    assert finding.component_id == "compose:x:app"
    assert finding.component_name == "app"
    assert finding.technology == "docker-compose"
    assert finding.source_file == "docker-compose.yml"
    assert "myapp:latest" in finding.reason


def test_route_computes_total_count(monkeypatch: pytest.MonkeyPatch) -> None:
    observations = [_finding_observation(component_id="compose:x:a"), _finding_observation(component_id="compose:x:b")]
    monkeypatch.setattr(security_service, "get_security_findings", lambda **kw: observations)

    request = SecurityAPIRequest(repo_url="https://github.com/example/repo")
    result = get_security_findings(request)

    assert result.total_count == 2
    assert len(result.findings) == 2


def test_route_computes_counts_by_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    observations = [
        _finding_observation(severity="high", component_id="compose:x:a"),
        _finding_observation(severity="high", component_id="compose:x:b"),
        _finding_observation(severity="medium", component_id="compose:x:c"),
    ]
    monkeypatch.setattr(security_service, "get_security_findings", lambda **kw: observations)

    request = SecurityAPIRequest(repo_url="https://github.com/example/repo")
    result = get_security_findings(request)

    assert result.counts_by_severity == {"high": 2, "medium": 1}


def test_route_returns_empty_response_when_no_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security_service, "get_security_findings", lambda **kw: [])

    request = SecurityAPIRequest(repo_url="https://github.com/example/repo")
    result = get_security_findings(request)

    assert result.findings == []
    assert result.total_count == 0
    assert result.counts_by_severity == {}


# --- request validation: repo_url / analysis_id XOR -----------------------------


def test_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    with pytest.raises(ValidationError):
        SecurityAPIRequest()


def test_request_rejects_both_repo_url_and_analysis_id() -> None:
    with pytest.raises(ValidationError):
        SecurityAPIRequest(repo_url="https://github.com/example/repo", analysis_id="some-id")


def test_request_accepts_repo_url_alone() -> None:
    request = SecurityAPIRequest(repo_url="https://github.com/example/repo")
    assert request.repo_url == "https://github.com/example/repo"
    assert request.analysis_id is None


def test_request_accepts_analysis_id_alone() -> None:
    request = SecurityAPIRequest(analysis_id="existing-analysis-id")
    assert request.analysis_id == "existing-analysis-id"
    assert request.repo_url is None


# --- error propagation -----------------------------------------------------------


def test_route_propagates_analysis_not_found_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import AnalysisNotFoundError

    def fake_get_findings(*, analysis_id=None, repo_url=None):
        raise AnalysisNotFoundError(analysis_id)

    monkeypatch.setattr(security_service, "get_security_findings", fake_get_findings)

    request = SecurityAPIRequest(analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_security_findings(request)


def test_route_propagates_invalid_repository_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions import InvalidRepositoryURLError

    def fake_get_findings(*, analysis_id=None, repo_url=None):
        raise InvalidRepositoryURLError("bad url")

    monkeypatch.setattr(security_service, "get_security_findings", fake_get_findings)

    request = SecurityAPIRequest(repo_url="not-a-github-url")
    with pytest.raises(InvalidRepositoryURLError):
        get_security_findings(request)


# --- app wiring -------------------------------------------------------------------


def test_app_registers_security_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/security" in paths


def test_app_still_registers_every_pre_existing_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/analyze" in paths
    assert "/api/v1/explain" in paths
    assert "/api/v1/components" in paths
    assert "/api/v1/impact" in paths
    assert "/api/v1/dependencies" in paths
    assert "/api/v1/graph/diagnostics" in paths
    assert "/api/v1/graph/path" in paths
