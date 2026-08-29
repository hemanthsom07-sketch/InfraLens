"""Application-level entry point for graph-wide structural diagnostics
and source/target path queries (Phase 7D).

Reuses GraphEngine.detect_cycles()/topological_order()/connected_components()/
shortest_path() exactly as-is (Phase 4/6) and
app.services.analysis_service.get_or_create_analysis() exactly as-is
(Phase 7A) — this module adds no new graph algorithm and no new caching
logic. It exists only to resolve a GraphEngine and call its own
already-tested methods, passing their results straight through.

ARCHITECTURAL RULE: like impact_service and dependency_service, this
module never imports networkx and never reaches into GraphEngine's
internals — every graph fact it touches comes through GraphEngine's own
public methods.
"""

from app.graph.engine import GraphEngine
from app.models.graph import Node
from app.services import analysis_service


class GraphDiagnosticsResult:
    """The three graph-wide structural diagnostics, bundled together
    since GraphEngine.detect_cycles()/topological_order()/
    connected_components() all take no extra parameters beyond which
    graph to resolve — a caller asking "is this graph healthy" naturally
    wants all three at once, not three separate round trips against the
    same resolved graph.

    Deliberately a plain class (not Pydantic), same reasoning as
    app.services.impact_service.MultiNodeImpactResult — this never
    crosses the API boundary directly; the router
    (app.api.v1.graph_diagnostics) maps it into a Pydantic response
    model.
    """

    __slots__ = ("cycles", "topological_order", "connected_components")

    def __init__(
        self,
        cycles: list[list[Node]],
        topological_order: list[Node] | None,
        connected_components: list[list[Node]],
    ) -> None:
        self.cycles = cycles
        self.topological_order = topological_order
        self.connected_components = connected_components


def get_graph_diagnostics(*, analysis_id: str | None = None, repo_url: str | None = None) -> GraphDiagnosticsResult:
    """detect_cycles(), topological_order(), and connected_components(),
    exactly as GraphEngine computes them — passed through unchanged, not
    reshaped or reinterpreted in any way.

    Note the scoping difference (unchanged GraphEngine/algorithm
    behavior, just worth restating here since it's easy to assume
    otherwise): detect_cycles()/topological_order() operate on the
    dependency-only subgraph (a depends_on-style cycle is what's
    actually infrastructure-breaking); connected_components() operates
    on the FULL graph, every edge type (a node joined to the rest only
    by a lateral connects_to edge is still meaningfully connected). This
    function does not change or normalize that difference — it reports
    exactly what each method already computes.

    `analysis_id`/`repo_url` follow analysis_service.get_or_create_analysis()'s
    resolution rule: give `analysis_id` to reuse an already-built graph,
    or `repo_url` to build a fresh one.

    Raises AnalysisNotFoundError (unknown/expired analysis_id) or
    InvalidRepositoryURLError/RepositoryCloneError (bad/unreachable
    repo_url) — unchanged from their respective owning layers. There is
    no NodeNotFoundError path here: all three diagnostics are graph-wide
    and take no node id.
    """
    engine = _resolve_engine(analysis_id=analysis_id, repo_url=repo_url)
    return GraphDiagnosticsResult(
        cycles=engine.detect_cycles(),
        topological_order=engine.topological_order(),
        connected_components=engine.connected_components(),
    )


def get_shortest_path(
    source_id: str, target_id: str, *, analysis_id: str | None = None, repo_url: str | None = None
) -> list[Node] | None:
    """The shortest node path from `source_id` to `target_id`, exactly
    as GraphEngine.shortest_path() computes it — None if they aren't
    connected. Unchanged scoping note: this considers every edge type,
    not just dependency edges (a request, or an attacker per the
    architecture doc's attack-surface use case, can travel through any
    relationship) — this function does not alter that.

    Raises AnalysisNotFoundError / InvalidRepositoryURLError /
    RepositoryCloneError exactly as get_graph_diagnostics() does above,
    plus NodeNotFoundError if `source_id` or `target_id` doesn't exist in
    the resolved graph — GraphEngine.shortest_path() validates both
    before computing (source checked first), not this function.
    """
    engine = _resolve_engine(analysis_id=analysis_id, repo_url=repo_url)
    return engine.shortest_path(source_id, target_id)


def _resolve_engine(*, analysis_id: str | None, repo_url: str | None) -> GraphEngine:
    """Shared resolution step for both functions above — the one place
    this module calls into analysis_service."""
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)
    return analysis.graph_engine
