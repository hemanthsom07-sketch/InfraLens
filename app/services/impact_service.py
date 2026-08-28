"""Application-level entry point for blast-radius / impact analysis
(Phase 7B).

Reuses GraphEngine.impact_analysis() exactly as-is (Phase 4/6) and
app.services.analysis_service.get_or_create_analysis() exactly as-is
(Phase 7A) — this module adds no new graph algorithm and no new caching
logic. Its only real work is the multi-node case: running
impact_analysis() once per requested node and combining the results,
which is plain set/dict aggregation over the Node lists GraphEngine
already produced, not a reimplementation of any traversal.

ARCHITECTURAL RULE: like explanation_service and component_lookup_service,
this module never imports networkx and never reaches into GraphEngine's
internals — every graph fact it touches comes through GraphEngine's own
public methods (impact_analysis()).
"""

from app.graph.engine import GraphEngine
from app.models.graph import ImpactReport, Node
from app.services import analysis_service


class MultiNodeImpactResult:
    """The combined result of running impact_analysis() for more than one
    node, plus each node's own individual ImpactReport for traceability.

    Deliberately a plain class (not Pydantic), same reasoning as
    app.services.component_lookup_service.ComponentListResult — this
    never crosses the API boundary directly; the router
    (app.api.v1.impact) maps it into a Pydantic response model.
    """

    __slots__ = (
        "targets",
        "direct_dependents",
        "transitive_dependents",
        "total_impact_count",
        "impact_by_type",
        "per_node",
    )

    def __init__(
        self,
        targets: list[Node],
        direct_dependents: list[Node],
        transitive_dependents: list[Node],
        total_impact_count: int,
        impact_by_type: dict[str, int],
        per_node: dict[str, ImpactReport],
    ) -> None:
        self.targets = targets
        self.direct_dependents = direct_dependents
        self.transitive_dependents = transitive_dependents
        self.total_impact_count = total_impact_count
        self.impact_by_type = impact_by_type
        self.per_node = per_node


def get_single_node_impact(
    node_id: str, *, analysis_id: str | None = None, repo_url: str | None = None
) -> ImpactReport:
    """The blast radius of a single node, exactly as
    GraphEngine.impact_analysis() computes it — returned unchanged, not
    reshaped in any way.

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
    return engine.impact_analysis(node_id)


def get_multi_node_impact(
    node_ids: list[str], *, analysis_id: str | None = None, repo_url: str | None = None
) -> MultiNodeImpactResult:
    """The combined blast radius of changing every node in `node_ids`
    together.

    Runs GraphEngine.impact_analysis() once per (de-duplicated) id — the
    only new logic here is combining those results:

    - A node is classified "direct" in the combined result if it's a
      direct dependent of ANY requested node, even if it's only a
      transitive dependent of another one — being one hop from any of
      the requested changes is what matters for "how soon would this be
      affected", so direct classification takes priority over transitive
      when a node is both for different targets.
    - `direct_dependents`/`transitive_dependents` are deduplicated by
      node id (a node affected by more than one requested change is
      reported once, not once per target it's a dependent of).
    - Target nodes are NOT filtered out of the dependents lists, even if
      one requested node happens to depend on another requested node
      (e.g. node_ids=["db", "backend"] where backend depends on db):
      this function reports exactly what GraphEngine.impact_analysis()
      computed for each input, combined — it does not invent a new
      "exclude the other inputs" rule GraphEngine itself doesn't have.
      This is a deliberate design choice, not an oversight.
    - `impact_by_type` is recomputed over the final deduplicated set, not
      summed across per-node reports — summing would double-count a node
      affected by more than one requested change.

    `per_node` holds each requested node's own unmodified ImpactReport
    (keyed by node id), so nothing is lost relative to calling
    get_single_node_impact() once per id separately — this is strictly
    additive traceability on top of the combined view above.

    Raises the same exceptions as get_single_node_impact(), including
    NodeNotFoundError for the first unknown id encountered while
    resolving `node_ids` (in the order given, after de-duplication).
    """
    engine = _resolve_engine(analysis_id=analysis_id, repo_url=repo_url)

    unique_ids = list(dict.fromkeys(node_ids))  # de-dup, preserve order
    per_node: dict[str, ImpactReport] = {node_id: engine.impact_analysis(node_id) for node_id in unique_ids}

    direct_by_id: dict[str, Node] = {}
    for report in per_node.values():
        for node in report.direct_dependents:
            direct_by_id[node.id] = node

    transitive_by_id: dict[str, Node] = {}
    for report in per_node.values():
        for node in report.transitive_dependents:
            if node.id not in direct_by_id:
                transitive_by_id[node.id] = node

    direct_dependents = sorted(direct_by_id.values(), key=lambda n: n.id)
    transitive_dependents = sorted(transitive_by_id.values(), key=lambda n: n.id)

    impact_by_type: dict[str, int] = {}
    for node in (*direct_dependents, *transitive_dependents):
        impact_by_type[node.node_type] = impact_by_type.get(node.node_type, 0) + 1

    targets = [per_node[node_id].target for node_id in unique_ids]

    return MultiNodeImpactResult(
        targets=targets,
        direct_dependents=direct_dependents,
        transitive_dependents=transitive_dependents,
        total_impact_count=len(direct_dependents) + len(transitive_dependents),
        impact_by_type=impact_by_type,
        per_node=per_node,
    )


def _resolve_engine(*, analysis_id: str | None, repo_url: str | None) -> GraphEngine:
    """Shared resolution step for both the single- and multi-node paths
    above — the one place this module calls into analysis_service."""
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)
    return analysis.graph_engine
