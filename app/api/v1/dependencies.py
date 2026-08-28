"""Endpoint: a component's dependencies (what it relies on) and
dependents (what relies on it), for a given repository or an
already-created analysis session.

ARCHITECTURAL RULE: this router does not compute dependencies itself —
it only validates the request shape and calls
app.services.dependency_service, which is the single entry point into
GraphEngine.get_dependencies()/get_dependents(). It never imports
networkx and never reaches into GraphEngine's internals.

Phase 7C: builds directly on Phase 7A/7B — accepts EITHER `repo_url`
(fresh clone/scan/build) OR `analysis_id` (reuse an already-built graph
from a prior POST /analyze), the same mutual-exclusivity validator
pattern already established in explain.py/components.py/impact.py.
Also accepts EITHER `node_id` (single-node dependencies+dependents) OR
`node_ids` (independent per-node breakdown for several nodes at once) —
exactly one of the two, same shape as impact.py's node_id/node_ids.

UNLIKE impact.py: multi-node mode here does NOT union/combine results.
Dependencies/dependents don't have an obviously correct single "combined"
meaning for a group of nodes the way blast radius does — so this
endpoint deliberately just returns each requested node's own
independent breakdown, keyed by node id, rather than inventing a
combining rule nobody asked for. See
app.services.dependency_service.get_multi_node_dependencies()'s
docstring for the full reasoning.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.models.graph import Node
from app.services import dependency_service

router = APIRouter()


class DependencyAPIRequest(BaseModel):
    """Request body for POST /dependencies.

    Exactly one of `node_id`/`node_ids`, and exactly one of
    `repo_url`/`analysis_id`, must be given — both enforced by this
    model's own validators below; FastAPI surfaces a failure of either
    as its normal 422 response.
    """

    node_id: str | None = Field(
        default=None, description="Dependencies/dependents of this single node. Mutually exclusive with node_ids."
    )
    node_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Independent per-node dependencies/dependents breakdown for each of these nodes. "
            "Mutually exclusive with node_id. Must be non-empty if given."
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
    def _check_exactly_one_node_source(self) -> "DependencyAPIRequest":
        if (self.node_id is None) == (self.node_ids is None):
            raise ValueError("Provide exactly one of node_id or node_ids.")
        return self

    @model_validator(mode="after")
    def _check_exactly_one_analysis_source(self) -> "DependencyAPIRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


class NodeDependencyResponse(BaseModel):
    """One node's dependencies (upstream) and dependents (downstream),
    exactly as GraphEngine.get_dependencies()/get_dependents() computed
    them — this is the whole response body for a single-node request,
    and one value in `nodes` for a multi-node request."""

    node_id: str
    dependencies: list[Node] = Field(default_factory=list, description="Everything this node (transitively) depends on.")
    dependents: list[Node] = Field(
        default_factory=list, description="Everything that (transitively) depends on this node."
    )


class MultiNodeDependencyResponse(BaseModel):
    """Response body for POST /dependencies when `node_ids` was given —
    each requested node's own independent NodeDependencyResponse, keyed
    by node id. No union, no deduplication across nodes — see this
    module's docstring for why."""

    nodes: dict[str, NodeDependencyResponse] = Field(
        ..., description="Each requested node's own independent dependency breakdown, keyed by node id."
    )


@router.post(
    "/dependencies",
    response_model=NodeDependencyResponse | MultiNodeDependencyResponse,
    summary="Dependencies (upstream) and dependents (downstream) of one or more components",
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
def get_dependencies(request: DependencyAPIRequest) -> NodeDependencyResponse | MultiNodeDependencyResponse:
    """Single-node requests (`node_id`) return one NodeDependencyResponse.
    Multi-node requests (`node_ids`) return a MultiNodeDependencyResponse
    with each node's own independent breakdown — no combining.

    A plain `def`, not `async def` — same reasoning as every other route
    in this API: resolving an analysis is blocking I/O in the fresh-clone
    case, and FastAPI runs sync handlers in a worker thread automatically.
    """
    if request.node_id is not None:
        result = dependency_service.get_single_node_dependencies(
            request.node_id, analysis_id=request.analysis_id, repo_url=request.repo_url
        )
        return NodeDependencyResponse(node_id=result.node_id, dependencies=result.dependencies, dependents=result.dependents)

    results = dependency_service.get_multi_node_dependencies(
        request.node_ids, analysis_id=request.analysis_id, repo_url=request.repo_url
    )
    return MultiNodeDependencyResponse(
        nodes={
            node_id: NodeDependencyResponse(
                node_id=result.node_id, dependencies=result.dependencies, dependents=result.dependents
            )
            for node_id, result in results.items()
        }
    )
