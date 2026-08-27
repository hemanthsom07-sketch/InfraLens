"""Application-level entry point for listing/searching components.

Phase 7A: resolves its GraphEngine through
app.services.analysis_service.get_or_create_analysis() instead of
cloning/scanning independently — this removes what used to be this
module's own private _build_graph_engine(), previously a deliberate,
documented near-duplicate of explanation_service's equivalent helper.
Both now share the one pipeline analysis_service owns, so the
duplication this module's docstring used to call out no longer exists.

`repo_url` and `analysis_id` follow
analysis_service.get_or_create_analysis()'s resolution rule: give
`analysis_id` to reuse an already-built graph from a prior POST
/analyze, or `repo_url` to build a fresh one — exactly one of the two.
Enforcing that shape is the API-layer request model's job
(app.api.v1.components.ComponentListRequest), not this module's.
"""

from app.services import analysis_service


class ComponentSummary:
    """A minimal, scannable component summary — deliberately not a full
    Node (no metadata dump). The point of this endpoint is discovering
    valid ids to pass to /explain, not a second /analyze."""

    __slots__ = ("id", "name", "node_type", "technology")

    def __init__(self, id: str, name: str, node_type: str, technology: str) -> None:
        self.id = id
        self.name = name
        self.node_type = node_type
        self.technology = technology


class ComponentListResult:
    """Phase 6C.7: a page of ComponentSummary results, plus enough
    pagination state for a caller to know whether there's more — `total`
    is always the count of every component matching the filters BEFORE
    pagination, never just len(items), so a caller can tell "3 of 250"
    apart from "3 of 3"."""

    __slots__ = ("items", "total", "limit", "offset", "has_more")

    def __init__(self, items: list[ComponentSummary], total: int, limit: int, offset: int) -> None:
        self.items = items
        self.total = total
        self.limit = limit
        self.offset = offset
        self.has_more = (offset + len(items)) < total


def list_components(
    repo_url: str | None = None,
    *,
    analysis_id: str | None = None,
    name_contains: str | None = None,
    technology: str | None = None,
    node_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> ComponentListResult:
    """List/search components in the graph resolved from `repo_url` or
    `analysis_id` (see module docstring for the resolution rule).

    All three filters are optional and combine with AND when more than
    one is given. `name_contains` is a case-insensitive substring match
    against each component's name. An empty result (not an error) means
    no match, including for a repository with no recognized
    infrastructure files at all.

    Pagination (Phase 6C.7): filtering happens first, against the full
    matching set — `total` in the returned ComponentListResult reflects
    that full count, never just how many are returned. `limit`/`offset`
    then slice a deterministic page out of it (components are already
    produced in a stable order upstream, so the same page is always the
    same components across repeated calls). Never silent: `has_more`
    always tells the caller whether there's more beyond this page.
    """
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)
    model = analysis.graph_engine.to_model()

    name_needle = name_contains.lower() if name_contains else None

    matching: list[ComponentSummary] = []
    for node in model.nodes:
        if name_needle is not None and name_needle not in node.name.lower():
            continue
        if technology is not None and node.technology != technology:
            continue
        if node_type is not None and node.node_type != node_type:
            continue
        matching.append(
            ComponentSummary(id=node.id, name=node.name, node_type=node.node_type, technology=node.technology)
        )

    total = len(matching)
    page = matching[offset : offset + limit]
    return ComponentListResult(items=page, total=total, limit=limit, offset=offset)
