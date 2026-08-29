"""Endpoints: graph-wide structural diagnostics (cycles, topological
order, connected components) and source/target shortest-path queries,
for a given repository or an already-created analysis session.

ARCHITECTURAL RULE: this router does not compute anything itself — it
only validates the request shape and calls
app.services.graph_diagnostics_service, the single entry point into
GraphEngine's diagnostic methods. It never imports networkx.

Phase 7D: two endpoints, not four and not one — see the design reasoning
below.

POST /graph/diagnostics bundles detect_cycles(), topological_order(),
and connected_components() into one response: all three are graph-wide
facts that take no extra parameters beyond which graph to resolve, so a
caller asking "is this graph healthy" naturally wants all three at once
against the same resolved graph, rather than three near-identical round
trips.

POST /graph/path is kept separate because shortest_path() has a
genuinely different, required two-node-id request shape (source_id AND
target_id, both mandatory) that doesn't fit the "just resolve the graph
and report facts about it" shape the diagnostics bundle has.

Both accept EITHER `repo_url` (fresh clone/scan/build) OR `analysis_id`
(reuse an already-built graph from a prior POST /analyze) — the same
mutual-exclusivity validator pattern already established in
explain.py/components.py/impact.py/dependencies.py.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.models.graph import Node
from app.services import graph_diagnostics_service

router = APIRouter()


class GraphDiagnosticsAPIRequest(BaseModel):
    """Request body for POST /graph/diagnostics — just an analysis
    source, no other parameters: all three diagnostics are graph-wide."""

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
    def _check_exactly_one_analysis_source(self) -> "GraphDiagnosticsAPIRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


class GraphDiagnosticsResponse(BaseModel):
    """Response body for POST /graph/diagnostics — detect_cycles(),
    topological_order(), and connected_components(), each exactly as
    GraphEngine computed them."""

    cycles: list[list[Node]] = Field(
        default_factory=list,
        description="Every dependency cycle, each an ordered list of Nodes. Empty if the graph is acyclic.",
    )
    is_acyclic: bool = Field(
        ..., description="True iff topological_order is not null (equivalently, cycles is empty)."
    )
    topological_order: list[Node] | None = Field(
        default=None, description="A valid dependency order, or null if the dependency subgraph has a cycle."
    )
    connected_components: list[list[Node]] = Field(
        default_factory=list,
        description=(
            "Every weakly-connected cluster of nodes across the FULL graph (every edge type). "
            "A length-1 group means that node is structurally isolated from everything else."
        ),
    )


class GraphPathAPIRequest(BaseModel):
    """Request body for POST /graph/path — a required source/target pair
    plus an analysis source."""

    source_id: str = Field(..., description="The node to find a path from.")
    target_id: str = Field(..., description="The node to find a path to.")
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
    def _check_exactly_one_analysis_source(self) -> "GraphPathAPIRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


class GraphPathResponse(BaseModel):
    """Response body for POST /graph/path."""

    source_id: str
    target_id: str
    connected: bool = Field(..., description="True iff a path exists between source_id and target_id.")
    path: list[Node] | None = Field(
        default=None,
        description=(
            "The shortest node path (any edge type, source to target) or null if they aren't "
            "connected."
        ),
    )


@router.post(
    "/graph/diagnostics",
    response_model=GraphDiagnosticsResponse,
    summary="Graph-wide structural diagnostics: cycles, topological order, connected components",
    responses={
        404: {"description": "The given analysis_id is unknown/expired."},
        422: {"description": "Invalid request: neither repo_url nor analysis_id given, or both given together."},
    },
)
def get_graph_diagnostics(request: GraphDiagnosticsAPIRequest) -> GraphDiagnosticsResponse:
    """A plain `def`, not `async def` — same reasoning as every other
    route in this API: resolving an analysis is blocking I/O in the
    fresh-clone case, and FastAPI runs sync handlers in a worker thread
    automatically."""
    result = graph_diagnostics_service.get_graph_diagnostics(
        analysis_id=request.analysis_id, repo_url=request.repo_url
    )
    return GraphDiagnosticsResponse(
        cycles=result.cycles,
        is_acyclic=result.topological_order is not None,
        topological_order=result.topological_order,
        connected_components=result.connected_components,
    )


@router.post(
    "/graph/path",
    response_model=GraphPathResponse,
    summary="Shortest node path between two components (any edge type)",
    responses={
        404: {
            "description": (
                "source_id or target_id does not exist in the graph, or the given analysis_id "
                "is unknown/expired."
            )
        },
        422: {"description": "Invalid request: neither repo_url nor analysis_id given, or both given together."},
    },
)
def get_graph_path(request: GraphPathAPIRequest) -> GraphPathResponse:
    path = graph_diagnostics_service.get_shortest_path(
        request.source_id, request.target_id, analysis_id=request.analysis_id, repo_url=request.repo_url
    )
    return GraphPathResponse(
        source_id=request.source_id, target_id=request.target_id, connected=path is not None, path=path
    )
