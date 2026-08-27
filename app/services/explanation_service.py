"""Application-level entry point for generating explanations (Phase 5).

Phase 7A: this module no longer clones/scans/builds independently.
Both functions resolve their GraphEngine through
app.services.analysis_service.get_or_create_analysis() — analysis_id
reuses an already-built graph if given (and still cached), otherwise
repo_url triggers a fresh clone/scan/build exactly as this module
always did before Phase 7A. This removes what used to be this module's
own private _build_graph_engine(), a near-duplicate of
component_lookup_service's equivalent private helper — both now share
the one pipeline analysis_service owns.

ARCHITECTURAL RULE: this module still does not gather evidence,
generate wording, compute confidence, or talk to an LLM provider itself
— all of that belongs to app.explanation.* (Stages 5B-5D) and is
unchanged by this phase. It exists only to resolve a GraphEngine (now
via analysis_service instead of its own clone) and hand it to Stage
5D's ExplanationEngine.
"""

from app.explanation.engine import ExplanationEngine
from app.models.explanation import ExplanationRequest, ExplanationResult
from app.services import analysis_service


def explain(
    repo_url: str | None,
    request: ExplanationRequest,
    *,
    analysis_id: str | None = None,
) -> ExplanationResult:
    """Explain a single node, or the relationship between two nodes.

    Covers: component, dependencies, dependents, impact, and
    architecture/connections (node_id requests); relationship
    (source_id/target_id requests) — unchanged from before Phase 7A.

    `repo_url` and `analysis_id` follow
    analysis_service.get_or_create_analysis()'s resolution rule: give
    `analysis_id` to reuse an already-built graph, or `repo_url` (with
    `analysis_id=None`) to build a fresh one, exactly as this function
    always did before Phase 7A when only repo_url existed. Which one to
    validate/require is the caller's (API-layer request model's)
    responsibility, not this function's.

    Raises AnalysisNotFoundError for an unknown/expired analysis_id,
    InvalidRepositoryURLError/RepositoryCloneError for a bad/unreachable
    repo_url, or NodeNotFoundError (propagated from Stage 5B/5D) for an
    unknown node id in the resolved graph.
    """
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)
    return ExplanationEngine(analysis.graph_engine).explain(request)


def explain_graph(repo_url: str | None, *, analysis_id: str | None = None) -> ExplanationResult:
    """Explain the whole graph. Covers: observations, cycles — unchanged
    from before Phase 7A. Resolution rule identical to explain() above."""
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)
    return ExplanationEngine(analysis.graph_engine).explain_graph()
