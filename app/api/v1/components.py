"""Endpoint: list/search components in a repository's graph, so a caller
can discover valid node ids before using POST /explain.

ARCHITECTURAL RULE: this router does not gather evidence, generate
wording, or talk to GraphEngine's internals directly — it only validates
the request shape and calls app.services.component_lookup_service, the
single entry point for this feature. It never imports networkx.

Phase 7A: the request body now accepts EITHER `repo_url` (fresh
clone/scan/build) OR `analysis_id` (reuse an already-built graph from a
prior POST /analyze, no clone/scan/build at all) — exactly one of the
two, enforced by ComponentListRequest's own validator below. This
supersedes the earlier "no caching or session concept" scope note for
this endpoint: it now shares the same analysis-session mechanism
/analyze and /explain use, via app.services.analysis_service.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.services import component_lookup_service

router = APIRouter()


class ComponentListRequest(BaseModel):
    """Request body for POST /components. All filters are optional and
    combine with AND when more than one is given. An analysis source
    (repo_url OR analysis_id) is required — exactly one of the two."""

    repo_url: str | None = Field(
        default=None, description="Public GitHub repository URL to analyze. Mutually exclusive with analysis_id."
    )
    analysis_id: str | None = Field(
        default=None,
        description=(
            "Id of an analysis session already created via POST /analyze (Phase 7A). "
            "Reuses that exact graph instead of triggering a fresh clone/scan. "
            "Mutually exclusive with repo_url."
        ),
    )
    name_contains: str | None = Field(
        default=None, description="Case-insensitive substring match against each component's name."
    )
    technology: str | None = Field(default=None, description="e.g. 'docker', 'docker-compose', 'kubernetes', 'terraform'.")
    node_type: str | None = Field(default=None, description="e.g. 'service', 'database', 'container'.")
    limit: int = Field(default=100, ge=1, le=500, description="Max components to return in this page.")
    offset: int = Field(default=0, ge=0, description="How many matching components to skip before this page.")

    @model_validator(mode="after")
    def _check_exactly_one_analysis_source(self) -> "ComponentListRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


class ComponentSummaryResponse(BaseModel):
    """One component's discoverable identity — deliberately not the full
    Node shape (no metadata dump); this is a scannable summary, not a
    second /analyze."""

    id: str
    name: str
    node_type: str
    technology: str


class ComponentListResponse(BaseModel):
    """Response body for POST /components.

    `total` is always the count of every component matching the filters
    — before pagination — never just len(components). `has_more` makes
    pagination state explicit rather than leaving a caller to infer it
    from comparing total to len(components) themselves.
    """

    components: list[ComponentSummaryResponse] = Field(default_factory=list)
    total: int = Field(..., description="Total components matching the filters, before pagination.")
    limit: int
    offset: int
    has_more: bool = Field(..., description="Whether more matching components exist beyond this page.")


@router.post(
    "/components",
    response_model=ComponentListResponse,
    summary="List/search components in a repository's graph",
    responses={
        404: {"description": "The given analysis_id is unknown/expired."},
        422: {"description": "Invalid request: neither repo_url nor analysis_id given, or both given together."},
    },
)
def list_components(request: ComponentListRequest) -> ComponentListResponse:
    """Discover valid node ids (and their name/type/technology) before
    calling POST /explain — an empty result means no match, not an
    error, including for a repository with no recognized infrastructure
    files at all.

    A plain `def`, not `async def` — same reasoning as
    analyze_repository/explain: cloning/scanning is blocking I/O, and
    FastAPI runs sync handlers in a worker thread automatically.
    """
    result = component_lookup_service.list_components(
        request.repo_url,
        analysis_id=request.analysis_id,
        name_contains=request.name_contains,
        technology=request.technology,
        node_type=request.node_type,
        limit=request.limit,
        offset=request.offset,
    )
    components = [
        ComponentSummaryResponse(id=s.id, name=s.name, node_type=s.node_type, technology=s.technology)
        for s in result.items
    ]
    return ComponentListResponse(
        components=components, total=result.total, limit=result.limit, offset=result.offset, has_more=result.has_more
    )
