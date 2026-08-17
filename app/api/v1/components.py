"""Endpoint: list/search components in a repository's graph, so a caller
can discover valid node ids before using POST /explain.

ARCHITECTURAL RULE: this router does not gather evidence, generate
wording, or talk to GraphEngine's internals directly — it only validates
the request shape and calls app.services.component_lookup_service, the
single entry point for this feature. It never imports networkx.

No caching or session/analysis-id concept (Phase 6A.4 scope): every call
clones and scans the repository fresh, exactly like POST /explain does
today. This is a read-only discovery convenience over the existing
per-call pattern, not a first step toward a broader caching layer.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import component_lookup_service

router = APIRouter()


class ComponentListRequest(BaseModel):
    """Request body for POST /components. All filters are optional and
    combine with AND when more than one is given."""

    repo_url: str = Field(..., description="Public GitHub repository URL to analyze.")
    name_contains: str | None = Field(
        default=None, description="Case-insensitive substring match against each component's name."
    )
    technology: str | None = Field(default=None, description="e.g. 'docker', 'docker-compose', 'kubernetes', 'terraform'.")
    node_type: str | None = Field(default=None, description="e.g. 'service', 'database', 'container'.")


class ComponentSummaryResponse(BaseModel):
    """One component's discoverable identity — deliberately not the full
    Node shape (no metadata dump); this is a scannable summary, not a
    second /analyze."""

    id: str
    name: str
    node_type: str
    technology: str


class ComponentListResponse(BaseModel):
    """Response body for POST /components."""

    components: list[ComponentSummaryResponse] = Field(default_factory=list)
    total: int = Field(..., description="len(components) — included so a caller doesn't need to count the list.")


@router.post(
    "/components",
    response_model=ComponentListResponse,
    summary="List/search components in a repository's graph",
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
    summaries = component_lookup_service.list_components(
        request.repo_url,
        name_contains=request.name_contains,
        technology=request.technology,
        node_type=request.node_type,
    )
    components = [
        ComponentSummaryResponse(id=s.id, name=s.name, node_type=s.node_type, technology=s.technology)
        for s in summaries
    ]
    return ComponentListResponse(components=components, total=len(components))
