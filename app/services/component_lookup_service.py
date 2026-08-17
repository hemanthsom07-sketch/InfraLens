"""Application-level entry point for listing/searching components.

Mirrors app.services.explanation_service's shape exactly: given a
repository URL, build the same GraphEngine /api/v1/analyze and
/api/v1/explain already build (clone -> scan -> IKM -> graph), then read
components off it.

DELIBERATELY SELF-CONTAINED: this module has its own private
_build_graph_engine(), duplicating the same ~10-line chain
explanation_service.py has, rather than sharing it. This is a conscious
choice (Phase 6A.4) to avoid restructuring already-shipped Stage 5E code
for this feature's sake — the duplication is small, contained to one
private function, and each copy is free to evolve independently without
one accidentally affecting the other's behavior. (Both this module and
explanation_service.py already import app.services.graph_service.build_graph
— that's Phase 4's existing public entry point into the Graph Engine,
not something new being added or shared here.)

Read-only, no caching or session/analysis-id concept: every call clones
and scans the repository fresh, exactly like /explain does today. This
endpoint is a convenience for discovering component ids before calling
/explain, not a first step toward a broader caching layer.
"""

import tempfile
from pathlib import Path

from app.graph.engine import GraphEngine
from app.services.git_service import clone_repository, parse_github_url
from app.services.graph_service import build_graph
from app.services.ikm_service import build_infrastructure_model
from app.services.scanner_service import scan_repository


def _build_graph_engine(repo_url: str) -> GraphEngine:
    """Clone `repo_url`, build its InfrastructureModel, and return a
    GraphEngine over it. Raises InvalidRepositoryURLError /
    RepositoryCloneError (app.exceptions), unchanged, for a bad or
    unreachable repository URL."""
    owner, repo = parse_github_url(repo_url)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        destination = Path(tmp_dir)
        clone_repository(owner, repo, destination)
        scan_result = scan_repository(destination)
        infrastructure_model = build_infrastructure_model(scan_result.file_paths, destination)
        return build_graph(infrastructure_model)


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


def list_components(
    repo_url: str,
    name_contains: str | None = None,
    technology: str | None = None,
    node_type: str | None = None,
) -> list[ComponentSummary]:
    """List/search components in the graph built from `repo_url`.

    All three filters are optional and combine with AND when more than
    one is given. `name_contains` is a case-insensitive substring match
    against each component's name. Returns an empty list (not an error)
    when nothing matches, including for a repository with no recognized
    infrastructure files at all.
    """
    graph_engine = _build_graph_engine(repo_url)
    model = graph_engine.to_model()

    name_needle = name_contains.lower() if name_contains else None

    summaries: list[ComponentSummary] = []
    for node in model.nodes:
        if name_needle is not None and name_needle not in node.name.lower():
            continue
        if technology is not None and node.technology != technology:
            continue
        if node_type is not None and node.node_type != node_type:
            continue
        summaries.append(
            ComponentSummary(id=node.id, name=node.name, node_type=node.node_type, technology=node.technology)
        )

    return summaries
