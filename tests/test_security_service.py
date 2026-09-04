"""Phase 7E: tests for app/services/security_service.py.
Phase 9: extended -- security_service now legitimately depends on
GraphEngine too, for the attack-surface check.

Verifies delegation to analysis_service (the same resolution pattern
every other service in this project uses), and verifies both finding
sources (the 9 metadata-only rules from app.security.rules, and Phase
9's app.security.attack_surface.check_attack_surface()) are merged into
one deterministically-ordered list.

IMPORTANT CORRECTION FROM PHASE 7E: this file used to test and document
that security_service "never touches GraphEngine" -- that was true
through Phase 7G and is no longer true as of Phase 9. The
_ExplodingGraphEngine tests below are updated accordingly, not silently
left claiming something false: a model with no Kubernetes exposed/
secret components still never triggers a graph_engine call (an
efficiency property worth keeping and testing), but a real Kubernetes
attack-surface scenario now genuinely requires and uses graph_engine.
"""

import pytest

from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import analysis_service, security_service
from tests.conftest import make_analysis_result


class _ExplodingGraphEngine:
    """Any attribute access raises -- used to prove a *particular*
    model never triggers a graph_engine call, not that security_service
    categorically never touches graph_engine (which is no longer true
    as of Phase 9)."""

    def __getattr__(self, name: str):
        raise AssertionError(f"graph_engine.{name} should not have been accessed for this model")


class _CountingShortestPathEngine:
    """Wraps a real GraphEngine, counting shortest_path() calls -- used
    to prove the attack-surface check genuinely calls into graph_engine
    for models that actually have Kubernetes exposed/secret data."""

    def __init__(self, real_engine: GraphEngine) -> None:
        self._real_engine = real_engine
        self.shortest_path_call_count = 0

    def shortest_path(self, source_id: str, target_id: str):
        self.shortest_path_call_count += 1
        return self._real_engine.shortest_path(source_id, target_id)

    def __getattr__(self, name: str):
        return getattr(self._real_engine, name)


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


def _k8s_component(kind: str, name: str, metadata: dict | None = None) -> Component:
    merged = {"source_file": "k8s.yaml", "kind": kind, **(metadata or {})}
    return Component(
        id=f"kubernetes:k8s.yaml:{kind}:{name}", name=name, type="kubernetes_resource",
        technology="kubernetes", metadata=merged,
    )


def _attack_surface_model() -> tuple[InfrastructureModel, GraphEngine]:
    """A real, minimal Kubernetes attack-surface scenario: TLS-less
    Ingress -> Service -> Deployment -> Secret. Built through the real
    GraphEngine/inference pipeline, exactly like tests/test_attack_surface.py."""
    ingress = _k8s_component("Ingress", "web-ingress", {"has_tls": False, "service_refs": ["web-svc"], "namespace": "prod"})
    service = _k8s_component("Service", "web-svc", {"service_type": "ClusterIP", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s_component("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s_component("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})

    components = [ingress, service, deployment, secret]
    relationships = [
        Relationship(source=ingress.id, target=service.id, relationship_type="connects_to"),
        Relationship(source=deployment.id, target=secret.id, relationship_type="uses"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    return model, engine


@pytest.fixture
def patched_resolve(monkeypatch: pytest.MonkeyPatch):
    def _patch(model: InfrastructureModel, graph_engine=None):
        result = make_analysis_result(
            graph_engine if graph_engine is not None else _ExplodingGraphEngine(),  # type: ignore[arg-type]
            infrastructure_model=model,
        )
        monkeypatch.setattr(
            analysis_service, "get_or_create_analysis", lambda *, analysis_id, repo_url: result
        )
        return result

    return _patch


# --- pre-existing (Phase 7E) behavior, still correct -----------------------------


def test_returns_findings_from_the_resolved_infrastructure_model(patched_resolve) -> None:
    patched_resolve(_model_with_finding())
    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "MUTABLE_IMAGE_TAG"


def test_returns_empty_list_for_a_clean_model(patched_resolve) -> None:
    patched_resolve(_model_with_no_findings())
    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")
    assert findings == []


def test_non_kubernetes_model_never_triggers_a_graph_engine_call(patched_resolve) -> None:
    """A compose-only model has no exposed entries and no Secrets, so
    check_attack_surface()'s own filters find nothing to check --
    graph_engine.shortest_path() is never called, and any other
    graph_engine access would raise via _ExplodingGraphEngine."""
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


def test_real_graph_engine_with_only_metadata_findings_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real (non-exploding) empty-model GraphEngine alongside a
    metadata-rule-triggering compose model -- confirms the Phase 9
    plumbing doesn't break the pre-existing 7E/7G behavior."""
    real_engine = GraphEngine.from_infrastructure_model(InfrastructureModel(), infer=True)
    result = make_analysis_result(real_engine, infrastructure_model=_model_with_finding())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", lambda *, analysis_id, repo_url: result)

    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")
    assert len(findings) == 1
    assert findings[0].detail["rule_id"] == "MUTABLE_IMAGE_TAG"


# --- Phase 9: attack-surface findings are merged --------------------------------


def test_attack_surface_findings_are_included(patched_resolve) -> None:
    model, engine = _attack_surface_model()
    patched_resolve(model, graph_engine=engine)

    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")

    rule_ids = {f.detail["rule_id"] for f in findings}
    assert "EXPOSED_PATH_TO_SECRET" in rule_ids


def test_graph_engine_shortest_path_is_actually_called_for_a_real_attack_surface_model(
    patched_resolve,
) -> None:
    """Proves the attack-surface check genuinely calls into
    graph_engine (not just that a finding happens to appear) -- the
    counting wrapper records at least one real shortest_path() call."""
    model, real_engine = _attack_surface_model()
    counting_engine = _CountingShortestPathEngine(real_engine)
    patched_resolve(model, graph_engine=counting_engine)

    security_service.get_security_findings(repo_url="https://github.com/example/repo")

    assert counting_engine.shortest_path_call_count > 0


def test_metadata_rules_and_attack_surface_findings_coexist(patched_resolve) -> None:
    """A model with BOTH a Compose mutable-tag issue AND a real
    Kubernetes attack-surface path produces findings from both sources,
    merged into one list.

    The fixture's Ingress has has_tls=False, which is also the exact,
    legitimate firing condition for the pre-existing (Phase 7G)
    INGRESS_WITHOUT_TLS rule -- so this model genuinely produces THREE
    findings (MUTABLE_IMAGE_TAG, INGRESS_WITHOUT_TLS,
    EXPOSED_PATH_TO_SECRET), not two. Asserting a hard-coded total here
    would be brittle and, worse, would silently mask a real rule firing
    correctly -- asserting presence of the specific rule_ids this test
    actually cares about is both more meaningful and more robust to any
    future fixture/rule additions that legitimately add further
    findings.
    """
    attack_model, engine = _attack_surface_model()
    compose_component = Component(
        id="compose:x:app", name="app", type="service", technology="docker-compose",
        metadata={"source_file": "docker-compose.yml", "image": "myapp:latest"},
    )
    combined_model = InfrastructureModel(
        components=[*attack_model.components, compose_component],
        relationships=attack_model.relationships,
    )
    patched_resolve(combined_model, graph_engine=engine)

    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")

    rule_ids = {f.detail["rule_id"] for f in findings}
    assert "MUTABLE_IMAGE_TAG" in rule_ids
    assert "EXPOSED_PATH_TO_SECRET" in rule_ids
    assert rule_ids == {"MUTABLE_IMAGE_TAG", "INGRESS_WITHOUT_TLS", "EXPOSED_PATH_TO_SECRET"}


def test_combined_output_is_deterministically_ordered(patched_resolve) -> None:
    attack_model, engine = _attack_surface_model()
    compose_component = Component(
        id="compose:x:app", name="app", type="service", technology="docker-compose",
        metadata={"source_file": "docker-compose.yml", "image": "myapp:latest"},
    )
    combined_model = InfrastructureModel(
        components=[*attack_model.components, compose_component],
        relationships=attack_model.relationships,
    )
    patched_resolve(combined_model, graph_engine=engine)

    first = [(f.subject_id, f.detail["rule_id"]) for f in security_service.get_security_findings(repo_url="https://github.com/example/repo")]

    patched_resolve(combined_model, graph_engine=engine)
    second = [(f.subject_id, f.detail["rule_id"]) for f in security_service.get_security_findings(repo_url="https://github.com/example/repo")]

    assert first == second
    assert first == sorted(first)


def test_no_attack_surface_finding_when_ingress_has_tls(patched_resolve) -> None:
    """Sanity check at the service layer (not just the rule/module
    layer): a TLS-enabled Ingress produces no EXPOSED_PATH_TO_SECRET
    finding through the full get_security_findings() path."""
    ingress = _k8s_component("Ingress", "web-ingress", {"has_tls": True, "service_refs": ["web-svc"], "namespace": "prod"})
    service = _k8s_component("Service", "web-svc", {"service_type": "ClusterIP", "selector": {"app": "web"}, "namespace": "prod"})
    deployment = _k8s_component("Deployment", "web", {"pod_labels": {"app": "web"}, "secret_refs": ["db-secret"], "namespace": "prod"})
    secret = _k8s_component("Secret", "db-secret", {"namespace": "prod", "secret_type": "Opaque", "has_data": True})
    components = [ingress, service, deployment, secret]
    relationships = [
        Relationship(source=ingress.id, target=service.id, relationship_type="connects_to"),
        Relationship(source=deployment.id, target=secret.id, relationship_type="uses"),
    ]
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    patched_resolve(model, graph_engine=engine)

    findings = security_service.get_security_findings(repo_url="https://github.com/example/repo")

    assert "EXPOSED_PATH_TO_SECRET" not in {f.detail["rule_id"] for f in findings}
