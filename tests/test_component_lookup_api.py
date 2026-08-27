"""Phase 6A.4 (base) + Phase 6C.7 (pagination) + Phase 7A (analysis
sessions): tests for app/services/component_lookup_service.py and
app/api/v1/components.py.

Phase 7A change: component_lookup_service no longer owns a private
_build_graph_engine() — it resolves its GraphEngine through
app.services.analysis_service.get_or_create_analysis(), exactly like
explanation_service now does. Tests that used to monkeypatch
component_lookup_service._build_graph_engine now monkeypatch
analysis_service.get_or_create_analysis instead; every assertion those
original tests made (filtering, pagination, route delegation) is
preserved, not weakened — and this file gains new coverage for the
analysis_id path Phase 7A adds.
"""

import pytest

from app.api.v1.components import ComponentListRequest, list_components as list_components_route
from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import analysis_service, component_lookup_service
from app.services.component_lookup_service import ComponentListResult, ComponentSummary
from tests.conftest import make_analysis_result


def _main_engine() -> GraphEngine:
    components = [
        Component(id="backend", name="backend", type="service", technology="docker-compose", metadata={}),
        Component(id="db", name="database", type="database", technology="docker-compose", metadata={}),
        Component(
            id="k8s-deploy", name="backend-deployment", type="kubernetes_resource", technology="kubernetes",
            metadata={"kind": "Deployment"},
        ),
    ]
    relationships = [Relationship(source="backend", target="db", relationship_type="depends_on")]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


def _many_components_engine(count: int) -> GraphEngine:
    components = [
        Component(id=f"svc-{i:04d}", name=f"svc-{i:04d}", type="service", technology="docker-compose", metadata={})
        for i in range(count)
    ]
    model = InfrastructureModel(components=components)
    return GraphEngine.from_infrastructure_model(model, infer=True)


@pytest.fixture
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> GraphEngine:
    graph = _main_engine()

    def fake_resolve(*, analysis_id, repo_url):
        return make_analysis_result(graph)

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)
    return graph


def _resolve_to(monkeypatch: pytest.MonkeyPatch, graph: GraphEngine) -> None:
    """Shorthand for the common case: whatever repo_url/analysis_id is
    given, resolve to `graph`."""
    monkeypatch.setattr(
        analysis_service, "get_or_create_analysis", lambda *, analysis_id, repo_url: make_analysis_result(graph)
    )


# --- service layer: filtering (regression, updated for the Phase 7A seam) -----


def test_list_components_returns_every_component_with_no_filters(patched_engine: GraphEngine) -> None:
    result = component_lookup_service.list_components("https://github.com/example/repo")
    assert {s.id for s in result.items} == {"backend", "db", "k8s-deploy"}


def test_list_components_filters_by_name_contains_case_insensitive(patched_engine: GraphEngine) -> None:
    result = component_lookup_service.list_components("https://github.com/example/repo", name_contains="BACK")
    assert {s.id for s in result.items} == {"backend", "k8s-deploy"}  # "backend-deployment" also matches "back"


def test_list_components_filters_by_technology(patched_engine: GraphEngine) -> None:
    result = component_lookup_service.list_components("https://github.com/example/repo", technology="kubernetes")
    assert {s.id for s in result.items} == {"k8s-deploy"}


def test_list_components_filters_by_node_type(patched_engine: GraphEngine) -> None:
    result = component_lookup_service.list_components("https://github.com/example/repo", node_type="database")
    assert {s.id for s in result.items} == {"db"}


def test_list_components_filters_combine_with_and(patched_engine: GraphEngine) -> None:
    result = component_lookup_service.list_components(
        "https://github.com/example/repo", technology="docker-compose", name_contains="back"
    )
    assert {s.id for s in result.items} == {"backend"}


def test_list_components_returns_empty_list_when_nothing_matches(patched_engine: GraphEngine) -> None:
    result = component_lookup_service.list_components("https://github.com/example/repo", name_contains="nope")
    assert result.items == []
    assert result.total == 0
    assert result.has_more is False


def test_list_components_on_empty_graph_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_engine = GraphEngine.from_infrastructure_model(InfrastructureModel(), infer=True)
    _resolve_to(monkeypatch, empty_engine)

    result = component_lookup_service.list_components("https://github.com/example/repo")
    assert result.items == []


# --- service layer: pagination (Phase 6C.7) ----------------------------------


def test_default_limit_is_100(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, _many_components_engine(150))
    result = component_lookup_service.list_components("https://github.com/example/repo")
    assert len(result.items) == 100
    assert result.total == 150
    assert result.has_more is True


def test_custom_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, _many_components_engine(50))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=10)
    assert len(result.items) == 10
    assert result.total == 50
    assert result.has_more is True


def test_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, _many_components_engine(30))
    first_page = component_lookup_service.list_components("https://github.com/example/repo", limit=10, offset=0)
    second_page = component_lookup_service.list_components("https://github.com/example/repo", limit=10, offset=10)
    assert {s.id for s in first_page.items}.isdisjoint({s.id for s in second_page.items})
    assert len(second_page.items) == 10


def test_has_more_false_on_last_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, _many_components_engine(25))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=10, offset=20)
    assert len(result.items) == 5
    assert result.has_more is False


def test_total_is_matching_count_not_returned_item_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact distinction the spec calls out: total must reflect
    every matching component, not len(items) after slicing."""
    _resolve_to(monkeypatch, _many_components_engine(42))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=5)
    assert len(result.items) == 5
    assert result.total == 42
    assert result.total != len(result.items)


def test_filters_and_pagination_combine(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, _main_engine())
    result = component_lookup_service.list_components(
        "https://github.com/example/repo", technology="docker-compose", limit=1
    )
    assert result.total == 2  # backend, db — both docker-compose
    assert len(result.items) == 1
    assert result.has_more is True


def test_limit_boundary_returns_exactly_total_when_equal(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, _many_components_engine(10))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=10)
    assert len(result.items) == 10
    assert result.has_more is False


def test_offset_beyond_total_returns_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_to(monkeypatch, _many_components_engine(5))
    result = component_lookup_service.list_components("https://github.com/example/repo", offset=100)
    assert result.items == []
    assert result.total == 5
    assert result.has_more is False


# --- service layer: analysis_id path (Phase 7A) --------------------------------


def test_list_components_with_analysis_id_passes_it_through_to_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = []

    def fake_resolve(*, analysis_id, repo_url):
        seen.append((repo_url, analysis_id))
        return make_analysis_result(_main_engine())

    monkeypatch.setattr(analysis_service, "get_or_create_analysis", fake_resolve)

    component_lookup_service.list_components(analysis_id="existing-analysis-id")

    assert seen == [(None, "existing-analysis-id")]


def test_list_components_with_analysis_id_returns_real_results(patched_engine: GraphEngine) -> None:
    result = component_lookup_service.list_components(analysis_id="existing-analysis-id")
    assert {s.id for s in result.items} == {"backend", "db", "k8s-deploy"}


def test_list_components_with_unknown_analysis_id_propagates_error() -> None:
    from app.exceptions import AnalysisNotFoundError

    with pytest.raises(AnalysisNotFoundError):
        component_lookup_service.list_components(analysis_id="ghost-id")


# --- API route -------------------------------------------------------------------


def test_route_delegates_to_service_and_returns_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_list_components(
        repo_url, *, analysis_id=None, name_contains=None, technology=None, node_type=None, limit=100, offset=0
    ):
        calls.append((repo_url, analysis_id, name_contains, technology, node_type, limit, offset))
        return ComponentListResult(
            items=[ComponentSummary("backend", "backend", "service", "docker-compose")], total=1, limit=limit, offset=offset
        )

    monkeypatch.setattr(component_lookup_service, "list_components", fake_list_components)

    request = ComponentListRequest(repo_url="https://github.com/example/repo", name_contains="back")
    response = list_components_route(request)

    assert response.total == 1
    assert response.components[0].id == "backend"
    assert calls == [("https://github.com/example/repo", None, "back", None, None, 100, 0)]


def test_route_delegates_analysis_id_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_list_components(
        repo_url, *, analysis_id=None, name_contains=None, technology=None, node_type=None, limit=100, offset=0
    ):
        calls.append((repo_url, analysis_id))
        return ComponentListResult(items=[], total=0, limit=limit, offset=offset)

    monkeypatch.setattr(component_lookup_service, "list_components", fake_list_components)

    request = ComponentListRequest(analysis_id="existing-analysis-id")
    list_components_route(request)

    assert calls == [(None, "existing-analysis-id")]


def test_route_returns_empty_list_and_zero_total_when_nothing_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        component_lookup_service,
        "list_components",
        lambda *a, **kw: ComponentListResult(items=[], total=0, limit=100, offset=0),
    )

    request = ComponentListRequest(repo_url="https://github.com/example/repo")
    response = list_components_route(request)

    assert response.components == []
    assert response.total == 0
    assert response.has_more is False


def test_route_passes_limit_and_offset_through(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_list_components(
        repo_url, *, analysis_id=None, name_contains=None, technology=None, node_type=None, limit=100, offset=0
    ):
        calls.append((limit, offset))
        return ComponentListResult(items=[], total=0, limit=limit, offset=offset)

    monkeypatch.setattr(component_lookup_service, "list_components", fake_list_components)

    request = ComponentListRequest(repo_url="https://github.com/example/repo", limit=25, offset=50)
    response = list_components_route(request)

    assert calls == [(25, 50)]
    assert response.limit == 25
    assert response.offset == 50


def test_route_response_includes_has_more(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        component_lookup_service,
        "list_components",
        lambda *a, **kw: ComponentListResult(items=[ComponentSummary("a", "a", "service", "docker-compose")], total=5, limit=1, offset=0),
    )
    request = ComponentListRequest(repo_url="https://github.com/example/repo")
    response = list_components_route(request)
    assert response.has_more is True


def test_request_only_requires_repo_url() -> None:
    request = ComponentListRequest(repo_url="https://github.com/example/repo")
    assert request.analysis_id is None
    assert request.name_contains is None
    assert request.technology is None
    assert request.node_type is None
    assert request.limit == 100
    assert request.offset == 0


def test_request_only_requires_analysis_id() -> None:
    request = ComponentListRequest(analysis_id="existing-analysis-id")
    assert request.repo_url is None
    assert request.analysis_id == "existing-analysis-id"


# --- request validation (Phase 6C.7 + Phase 7A) --------------------------------


def test_invalid_negative_limit_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComponentListRequest(repo_url="https://github.com/example/repo", limit=-1)


def test_invalid_zero_limit_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComponentListRequest(repo_url="https://github.com/example/repo", limit=0)


def test_invalid_excessive_limit_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComponentListRequest(repo_url="https://github.com/example/repo", limit=501)


def test_invalid_negative_offset_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComponentListRequest(repo_url="https://github.com/example/repo", offset=-1)


def test_max_valid_limit_accepted() -> None:
    request = ComponentListRequest(repo_url="https://github.com/example/repo", limit=500)
    assert request.limit == 500


def test_request_rejects_neither_repo_url_nor_analysis_id() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComponentListRequest()


def test_request_rejects_both_repo_url_and_analysis_id() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComponentListRequest(repo_url="https://github.com/example/repo", analysis_id="some-id")


# --- app wiring --------------------------------------------------------------------


def test_app_registers_components_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/components" in paths
