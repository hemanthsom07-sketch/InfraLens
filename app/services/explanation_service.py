"""Application-level entry point for generating explanations.

Mirrors app.api.v1.analyze.analyze_repository's shape: given a
repository URL, build the same GraphEngine /api/v1/analyze already
builds (clone -> scan -> IKM -> graph), then hand it to Stage 5D's
ExplanationEngine.

ARCHITECTURAL RULE: this module does not gather evidence, generate
wording, compute confidence, or talk to an LLM provider itself — all of
that already belongs to app.explanation.* (Stages 5B-5D). It exists only
to wire a repository URL to an ExplanationEngine call and return the
result, following graph_service.py's pattern of being "a thin entry
point" one layer above the thing it calls.
"""

import tempfile
from pathlib import Path

from app.explanation.engine import ExplanationEngine
from app.graph.engine import GraphEngine
from app.models.explanation import ExplanationRequest, ExplanationResult
from app.services.git_service import clone_repository, parse_github_url
from app.services.graph_service import build_graph
from app.services.ikm_service import build_infrastructure_model
from app.services.scanner_service import scan_repository


def _build_graph_engine(repo_url: str) -> GraphEngine:
    """Clone `repo_url`, build its InfrastructureModel, and return a
    GraphEngine over it.

    Same pipeline app.api.v1.analyze.analyze_repository uses up through
    the graph, minus the framework/infrastructure-detection steps
    AnalyzeResponse needs but an explanation doesn't. Raises
    InvalidRepositoryURLError / RepositoryCloneError (app.exceptions),
    unchanged, for a bad or unreachable repository URL.
    """
    owner, repo = parse_github_url(repo_url)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        destination = Path(tmp_dir)
        clone_repository(owner, repo, destination)
        scan_result = scan_repository(destination)
        infrastructure_model = build_infrastructure_model(scan_result.file_paths, destination)
        return build_graph(infrastructure_model)


def explain(repo_url: str, request: ExplanationRequest) -> ExplanationResult:
    """Explain a single node, or the relationship between two nodes, in
    the graph built from `repo_url`.

    Covers: component, dependencies, dependents, impact,
    architecture/connections (node_id requests); relationship
    (source_id/target_id requests).

    Raises NodeNotFoundError (app.graph.exceptions), propagated
    unchanged from Stage 5B/5D, if the requested node id(s) don't exist
    in the graph.
    """
    graph_engine = _build_graph_engine(repo_url)
    return ExplanationEngine(graph_engine).explain(request)


def explain_graph(repo_url: str) -> ExplanationResult:
    """Explain the whole graph built from `repo_url`.

    Covers: observations, cycles.
    """
    graph_engine = _build_graph_engine(repo_url)
    return ExplanationEngine(graph_engine).explain_graph()
