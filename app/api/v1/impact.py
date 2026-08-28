"""Endpoint: the blast radius (direct + transitive dependents) of one or
more components, for a given repository or an already-created analysis
session.

ARCHITECTURAL RULE: this router does not compute impact itself — it only
validates the request shape and calls app.services.impact_service, which
is the single entry point into GraphEngine.impact_analysis(). It never
imports networkx and never reaches into GraphEngine's internals.

Phase 7B: builds directly on Phase 7A — accepts EITHER `repo_url` (fresh
clone/scan/build) OR `analysis_id` (reuse an already-built graph from a
prior POST /analyze), exactly the same mutual-exclusivity validator
pattern already established in app.api.v1.explain and
app.api.v1.components. Additionally accepts EITHER `node_id` (single-node
blast radius, returned as GraphEngine's own ImpactReport unchanged) OR
`node_ids` (combined multi-node blast radius) — exactly one of the two.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.models.graph import ImpactReport, Node
from app.services import impact_service

router = APIRouter()


class ImpactAPIRequest(BaseModel):
    """Request body for POST /impact.

    Exactly one of `node_id`/`node_ids`, and exactly one of
    `repo_url`/`analysis_id`, must be given — both enforced by this
    model's own validators below; FastAPI surfaces a failure of either
    as its normal 422 response.
    """

    node_id: str | None = Field(
        default=None, description="Blast radius of this single node. Mutually exclusive with node_ids."
    )
    node_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Combined blast radius of changing all of these nodes together. Mutually exclusive "
            "with node_id. Must be non-empty if given."
        ),
    )
    repo_url: str | None = Field(
        default=None, description="Public GitHub repository URL to analyze. Mutually exclusive with analysis_id."
    )
    analysis_id: str | None = Field(
        default=None,
        description=(
            "Id of an analysis session already created via POST /analyze (Phase 7A). Reuses "
            "that exact graph instead of triggering a fresh clone/scan. Mutually exclusive with "
            "repo_url."
        ),
    )

    @model_validator(mode="after")
    def _check_exactly_one_node_source(self) -> "ImpactAPIRequest":
        if (self.node_id is None) == (self.node_ids is None):
            raise ValueError("Provide exactly one of node_id or node_ids.")
        return self

    @model_validator(mode="after")
    def _check_exactly_one_analysis_source(self) -> "ImpactAPIRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


class MultiNodeImpactResponse(BaseModel):
    """Response body for POST /impact when `node_ids` was given — the
    combined blast radius of changing every requested node together,
    plus each node's own individual ImpactReport for traceability. See
    app.services.impact_service.get_multi_node_impact()'s docstring for
    exactly how the union/deduplication/classification rules work.
    """

    targets: list[Node] = Field(..., description="The requested nodes, in the order given (after de-duplication).")
    direct_dependents: list[Node] = Field(
        default_factory=list, description="Union of every target's direct dependents, deduplicated by node id."
    )
    transitive_dependents: list[Node] = Field(
        default_factory=list,
        description=(
            "Union of every target's transitive dependents, deduplicated by node id and excluding "
            "any node already counted as a direct dependent of some other target."
        ),
    )
    total_impact_count: int = Field(..., description="len(direct_dependents) + len(transitive_dependents).")
    impact_by_type: dict[str, int] = Field(
        default_factory=dict, description="Computed over the final deduplicated set, not summed per-node."
    )
    per_node: dict[str, ImpactReport] = Field(
        ..., description="Each requested node's own unmodified ImpactReport, keyed by node id."
    )


@router.post(
    "/impact",
    response_model=ImpactReport | MultiNodeImpactResponse,
    summary="Blast radius (direct + transitive dependents) of one or more components",
    responses={
        404: {
            "description": (
                "One of the requested node ids does not exist in the graph, or the given "
                "analysis_id is unknown/expired."
            )
        },
        422: {
            "description": (
                "Invalid request: neither node_id nor node_ids given, or both given together "
                "(or node_ids is empty); or neither repo_url nor analysis_id given, or both "
                "given together."
            )
        },
    },
)
def get_impact(request: ImpactAPIRequest) -> ImpactReport | MultiNodeImpactResponse:
    """Single-node requests (`node_id`) return
    GraphEngine.impact_analysis()'s own ImpactReport unchanged — no
    reshaping, no duplicated logic. Multi-node requests (`node_ids`)
    return the combined MultiNodeImpactResponse above.

    A plain `def`, not `async def` — same reasoning as every other route
    in this API: resolving an analysis is blocking I/O in the fresh-clone
    case, and FastAPI runs sync handlers in a worker thread automatically.
    """
    if request.node_id is not None:
        return impact_service.get_single_node_impact(
            request.node_id, analysis_id=request.analysis_id, repo_url=request.repo_url
        )

    result = impact_service.get_multi_node_impact(
        request.node_ids, analysis_id=request.analysis_id, repo_url=request.repo_url
    )
    return MultiNodeImpactResponse(
        targets=result.targets,
        direct_dependents=result.direct_dependents,
        transitive_dependents=result.transitive_dependents,
        total_impact_count=result.total_impact_count,
        impact_by_type=result.impact_by_type,
        per_node=result.per_node,
    )
