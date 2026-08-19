"""Phase 6A.4 (base) + Phase 6C.7 (pagination): tests for
app/services/component_lookup_service.py and app/api/v1/components.py.

Same no-network-call pattern established for /explain's tests:
_build_graph_engine() does a real `git clone`, which this sandbox can't
do and which existing tests avoid too, so it's monkeypatched to return a
hand-built GraphEngine instead. No httpx/TestClient — the route handler
is a plain, synchronous Python function, called directly.

Phase 6C.7 changed list_components()'s return shape from a plain list to
a ComponentListResult (.items/.total/.limit/.offset/.has_more) — every
existing test here was updated accordingly, not weakened; the assertions
now check MORE (pagination state) than before, not less.
"""

from pathlib import Path

import pytest

from app.api.v1.components import ComponentListRequest, list_components as list_components_route
from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import component_lookup_service
from app.services.component_lookup_service import ComponentListResult, ComponentSummary


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

    def fake_build(repo_url: str) -> GraphEngine:
        return graph

    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", fake_build)
    return graph


# --- service layer: filtering (regression, updated for the new return shape) --


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
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: empty_engine)

    result = component_lookup_service.list_components("https://github.com/example/repo")
    assert result.items == []


# --- service layer: pagination (Phase 6C.7) ----------------------------------


def test_default_limit_is_100(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: _many_components_engine(150))
    result = component_lookup_service.list_components("https://github.com/example/repo")
    assert len(result.items) == 100
    assert result.total == 150
    assert result.has_more is True


def test_custom_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: _many_components_engine(50))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=10)
    assert len(result.items) == 10
    assert result.total == 50
    assert result.has_more is True


def test_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: _many_components_engine(30))
    first_page = component_lookup_service.list_components("https://github.com/example/repo", limit=10, offset=0)
    second_page = component_lookup_service.list_components("https://github.com/example/repo", limit=10, offset=10)
    assert {s.id for s in first_page.items}.isdisjoint({s.id for s in second_page.items})
    assert len(second_page.items) == 10


def test_has_more_false_on_last_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: _many_components_engine(25))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=10, offset=20)
    assert len(result.items) == 5
    assert result.has_more is False


def test_total_is_matching_count_not_returned_item_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact distinction the spec calls out: total must reflect
    every matching component, not len(items) after slicing."""
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: _many_components_engine(42))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=5)
    assert len(result.items) == 5
    assert result.total == 42
    assert result.total != len(result.items)


def test_filters_and_pagination_combine(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _main_engine()
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: engine)
    result = component_lookup_service.list_components(
        "https://github.com/example/repo", technology="docker-compose", limit=1
    )
    assert result.total == 2  # backend, db — both docker-compose
    assert len(result.items) == 1
    assert result.has_more is True


def test_limit_boundary_returns_exactly_total_when_equal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: _many_components_engine(10))
    result = component_lookup_service.list_components("https://github.com/example/repo", limit=10)
    assert len(result.items) == 10
    assert result.has_more is False


def test_offset_beyond_total_returns_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: _many_components_engine(5))
    result = component_lookup_service.list_components("https://github.com/example/repo", offset=100)
    assert result.items == []
    assert result.total == 5
    assert result.has_more is False


# --- API route -------------------------------------------------------------------


def test_route_delegates_to_service_and_returns_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_list_components(repo_url, name_contains=None, technology=None, node_type=None, limit=100, offset=0):
        calls.append((repo_url, name_contains, technology, node_type, limit, offset))
        return ComponentListResult(
            items=[ComponentSummary("backend", "backend", "service", "docker-compose")], total=1, limit=limit, offset=offset
        )

    monkeypatch.setattr(component_lookup_service, "list_components", fake_list_components)

    request = ComponentListRequest(repo_url="https://github.com/example/repo", name_contains="back")
    response = list_components_route(request)

    assert response.total == 1
    assert response.components[0].id == "backend"
    assert calls == [("https://github.com/example/repo", "back", None, None, 100, 0)]


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

    def fake_list_components(repo_url, name_contains=None, technology=None, node_type=None, limit=100, offset=0):
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
    assert request.name_contains is None
    assert request.technology is None
    assert request.node_type is None
    assert request.limit == 100
    assert request.offset == 0


# --- request validation (Phase 6C.7) ------------------------------------------


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


# --- app wiring --------------------------------------------------------------------


def test_app_registers_components_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/components" in paths
