"""Phase 6A.4: tests for app/services/component_lookup_service.py and
app/api/v1/components.py.

Same no-network-call pattern established for /explain's tests
(test_explanation_service.py, test_explanation_api.py):
_build_graph_engine() does a real `git clone`, which this sandbox can't
do and which existing tests avoid too, so it's monkeypatched to return a
hand-built GraphEngine instead. No httpx/TestClient — the route handler
is a plain, synchronous Python function, called directly.
"""

from pathlib import Path

import pytest

from app.api.v1.components import ComponentListRequest, list_components as list_components_route
from app.graph.engine import GraphEngine
from app.models.ikm import Component, InfrastructureModel, Relationship
from app.services import component_lookup_service


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


@pytest.fixture
def patched_engine(monkeypatch: pytest.MonkeyPatch) -> GraphEngine:
    graph = _main_engine()

    def fake_build(repo_url: str) -> GraphEngine:
        return graph

    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", fake_build)
    return graph


# --- service layer -------------------------------------------------------------


def test_list_components_returns_every_component_with_no_filters(patched_engine: GraphEngine) -> None:
    summaries = component_lookup_service.list_components("https://github.com/example/repo")
    assert {s.id for s in summaries} == {"backend", "db", "k8s-deploy"}


def test_list_components_filters_by_name_contains_case_insensitive(patched_engine: GraphEngine) -> None:
    summaries = component_lookup_service.list_components("https://github.com/example/repo", name_contains="BACK")
    assert {s.id for s in summaries} == {"backend", "k8s-deploy"}  # "backend-deployment" also matches "back"


def test_list_components_filters_by_technology(patched_engine: GraphEngine) -> None:
    summaries = component_lookup_service.list_components("https://github.com/example/repo", technology="kubernetes")
    assert {s.id for s in summaries} == {"k8s-deploy"}


def test_list_components_filters_by_node_type(patched_engine: GraphEngine) -> None:
    summaries = component_lookup_service.list_components("https://github.com/example/repo", node_type="database")
    assert {s.id for s in summaries} == {"db"}


def test_list_components_filters_combine_with_and(patched_engine: GraphEngine) -> None:
    summaries = component_lookup_service.list_components(
        "https://github.com/example/repo", technology="docker-compose", name_contains="back"
    )
    assert {s.id for s in summaries} == {"backend"}


def test_list_components_returns_empty_list_when_nothing_matches(patched_engine: GraphEngine) -> None:
    summaries = component_lookup_service.list_components("https://github.com/example/repo", name_contains="nope")
    assert summaries == []


def test_list_components_on_empty_graph_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    empty_engine = GraphEngine.from_infrastructure_model(InfrastructureModel(), infer=True)
    monkeypatch.setattr(component_lookup_service, "_build_graph_engine", lambda repo_url: empty_engine)

    summaries = component_lookup_service.list_components("https://github.com/example/repo")
    assert summaries == []


# --- API route -------------------------------------------------------------------


def test_route_delegates_to_service_and_returns_summaries(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class _FakeSummary:
        def __init__(self, id, name, node_type, technology):
            self.id, self.name, self.node_type, self.technology = id, name, node_type, technology

    def fake_list_components(repo_url, name_contains=None, technology=None, node_type=None):
        calls.append((repo_url, name_contains, technology, node_type))
        return [_FakeSummary("backend", "backend", "service", "docker-compose")]

    monkeypatch.setattr(component_lookup_service, "list_components", fake_list_components)

    request = ComponentListRequest(repo_url="https://github.com/example/repo", name_contains="back")
    response = list_components_route(request)

    assert response.total == 1
    assert response.components[0].id == "backend"
    assert calls == [("https://github.com/example/repo", "back", None, None)]


def test_route_returns_empty_list_and_zero_total_when_nothing_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(component_lookup_service, "list_components", lambda *a, **kw: [])

    request = ComponentListRequest(repo_url="https://github.com/example/repo")
    response = list_components_route(request)

    assert response.components == []
    assert response.total == 0


def test_request_only_requires_repo_url() -> None:
    request = ComponentListRequest(repo_url="https://github.com/example/repo")
    assert request.name_contains is None
    assert request.technology is None
    assert request.node_type is None


# --- app wiring --------------------------------------------------------------------


def test_app_registers_components_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/components" in paths
