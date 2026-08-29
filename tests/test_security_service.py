"""Phase 7E: tests for app/services/security_service.py.

Verifies delegation to analysis_service (the same resolution pattern
every other service in this project uses) and confirms this service
reads analysis.infrastructure_model directly rather than ever touching
GraphEngine -- proven by using an AnalysisResult whose graph_engine is
a deliberately broken sentinel that would raise if anything tried to
call a method on it.
"""

import pytest

from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel
from app.services import analysis_service, security_service
from tests.conftest import make_analysis_result


class _ExplodingGraphEngine:
    """Any attribute access raises -- proves security_service never
    touches graph_engine at all, not even to read an attribute."""

    def __getattr__(self, name: str):
        raise AssertionError(f"security_service must never access GraphEngine.{name}")


def _model_with_finding() -> InfrastructureModel:
    return InfrastructureModel(
        components=[
            Component(
                id="compose:x:app", name="app", type="service", technology="docker-compose",
                metadata={"source_file": "docker-compose.yml", "image": "myapp:latest"},
            )
        ]
    )


def _model_with_no_findings() -> InfrastructureModel:
    return InfrastructureModel(
        components=[
            Component(
                id="compose:x:app", name="app", type="service", technology="docker-compose",
                metadata={"source_file": "docker-compose.yml", "image": "myapp:1.0", "ports": [], "environment": {}},
            )
        ]
    )


@pytest.fixture
def patched_resolve(monkeypatch: pytest.MonkeyPatch):
    def _patch(model: InfrastructureModel):
        result = make_analysis_result(
            _ExplodingGraphEngine(),  # type: ignore[arg-type]
            infrastructure_model=model,
        )
        monkeypatch.setattr(
            analysis_service, "get_or_create_analysis", lambda *, analysis_id, repo_url: result
        )
        return result

    return _patch


def test_returns_findings_from_the_resolved_infrastructure_model(patched_resolve) -> None:
    patched_resolve(_model_with_finding())
    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "MUTABLE_IMAGE_TAG"


def test_returns_empty_list_for_a_clean_model(patched_resolve) -> None:
    patched_resolve(_model_with_no_findings())
    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")
    assert findings == []


def test_never_touches_graph_engine(patched_resolve) -> None:
    """The AnalysisResult's graph_engine is an _ExplodingGraphEngine --
    if security_service ever accessed it, this test would raise instead
    of passing."""
    patched_resolve(_model_with_finding())
    security_service.get_security_findings(repo_url="https://github.com/example/repo")  # must not raise


def test_passes_repo_url_through_to_analysis_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_ExplodingGraphEngine(), infrastructure_model=_model_with_no_findings())  # type: ignore[arg-type]

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    security_service.get_security_findings(repo_url="https://github.com/example/repo")

    assert seen == [("https://github.com/example/repo", None)]


def test_passes_analysis_id_through_to_analysis_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_ExplodingGraphEngine(), infrastructure_model=_model_with_no_findings())  # type: ignore[arg-type]

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    security_service.get_security_findings(analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


def test_unknown_analysis_id_raises_analysis_not_found_error() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        security_service.get_security_findings(analysis_id="ghost-id")


def test_real_graph_engine_is_never_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine end-to-end sanity check using a real (non-exploding)
    GraphEngine built from an empty model, confirming security_service
    works correctly even when a real engine happens to be present --
    it simply never looks at it."""
    real_engine = GraphEngine.from_infrastructure_model(InfrastructureModel(), infer=True)
    result = make_analysis_result(real_engine, infrastructure_model=_model_with_finding())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", lambda *, analysis_id, repo_url: result)

    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")
    assert len(findings) == 1
