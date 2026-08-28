"""Application-level entry point for structured dependency queries
(Phase 7C).

Reuses GraphEngine.get_dependencies()/get_dependents() exactly as-is
(Phase 4/6) and app.services.analysis_service.get_or_create_analysis()
exactly as-is (Phase 7A) — this module adds no new graph algorithm and
no new caching logic. Unlike Phase 7B's impact_service, there is no
combining/union step for the multi-node case: dependencies/dependents
don't have a meaningful single "combined" semantic the way blast radius
does (see app.api.v1.dependencies module docstring), so multi-node mode
here is intentionally just "run the same two calls per requested node",
nothing more invented.

ARCHITECTURAL RULE: like impact_service, explanation_service, and
component_lookup_service, this module never imports networkx and never
reaches into GraphEngine's internals — every graph fact it touches comes
through GraphEngine's own public methods.
"""

from app.graph.engine import GraphEngine
from app.models.graph import Node
from app.services import analysis_service


class NodeDependencyResult:
    """One node's dependencies (upstream — what it relies on) and
    dependents (downstream — what relies on it), both exactly as
    GraphEngine.get_dependencies()/get_dependents() computed them.

    Deliberately a plain class (not Pydantic), same reasoning as
    app.services.impact_service.MultiNodeImpactResult and
    app.services.component_lookup_service.ComponentListResult — this
    never crosses the API boundary directly; the router
    (app.api.v1.dependencies) maps it into a Pydantic response model.
    """

    __slots__ = ("node_id", "dependencies", "dependents")

    def __init__(self, node_id: str, dependencies: list[Node], dependents: list[Node]) -> None:
        self.node_id = node_id
        self.dependencies = dependencies
        self.dependents = dependents


def get_single_node_dependencies(
    node_id: str, *, analysis_id: str | None = None, repo_url: str | None = None
) -> NodeDependencyResult:
    """`node_id`'s dependencies and dependents, exactly as
    GraphEngine.get_dependencies()/get_dependents() compute them —
    passed through unchanged, not reshaped in any way.

    `analysis_id`/`repo_url` follow analysis_service.get_or_create_analysis()'s
    resolution rule: give `analysis_id` to reuse an already-built graph,
    or `repo_url` to build a fresh one. Which one to require is the
    caller's (API-layer request model's) responsibility, not this
    function's.

    Raises AnalysisNotFoundError (unknown/expired analysis_id),
    InvalidRepositoryURLError/RepositoryCloneError (bad/unreachable
    repo_url), or NodeNotFoundError (unknown node id in the resolved
    graph) — all unchanged from their respective owning layers.
    """
    engine = _resolve_engine(analysis_id=analysis_id, repo_url=repo_url)
    return _single_result(engine, node_id)


def get_multi_node_dependencies(
    node_ids: list[str], *, analysis_id: str | None = None, repo_url: str | None = None
) -> dict[str, NodeDependencyResult]:
    """Each requested node's own dependencies/dependents, as an
    independent per-node breakdown — no union, no deduplication, no
    combined view invented. Unlike blast radius (Phase 7B), there's no
    obviously correct single "combined dependency set" for a group of
    nodes (should it be the union of what any of them depends on? the
    intersection of shared prerequisites? something else?) — rather than
    guess at a semantic nobody asked for, this simply runs
    get_single_node_dependencies() once per (de-duplicated) requested
    id and returns the results keyed by node id.

    Raises the same exceptions as get_single_node_dependencies(),
    including NodeNotFoundError for the first unknown id encountered
    while resolving `node_ids` (in the order given, after
    de-duplication).
    """
    engine = _resolve_engine(analysis_id=analysis_id, repo_url=repo_url)
    unique_ids = list(dict.fromkeys(node_ids))  # de-dup, preserve order
    return {node_id: _single_result(engine, node_id) for node_id in unique_ids}


def _single_result(engine: GraphEngine, node_id: str) -> NodeDependencyResult:
    dependencies = engine.get_dependencies(node_id)
    dependents = engine.get_dependents(node_id)
    return NodeDependencyResult(node_id=node_id, dependencies=dependencies, dependents=dependents)


def _resolve_engine(*, analysis_id: str | None, repo_url: str | None) -> GraphEngine:
    """Shared resolution step for both the single- and multi-node paths
    above — the one place this module calls into analysis_service."""
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)
    return analysis.graph_engine
