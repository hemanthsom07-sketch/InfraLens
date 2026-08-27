"""Endpoint: clone a public GitHub repository, scan its contents, and
detect its languages, frameworks, infrastructure tooling, structured
Infrastructure Knowledge Model, and queryable dependency graph.

Phase 7A: this route now delegates the entire clone -> scan -> parse ->
build pipeline to app.services.analysis_service, which also caches the
result under a new analysis_id (returned in the response) so /explain,
/explain/graph, and /components can reuse this exact graph afterward
instead of triggering another clone. The route itself does no I/O and
no pipeline orchestration anymore — that's analysis_service's job now,
not this router's.
"""

from fastapi import APIRouter

from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.services import analysis_service

router = APIRouter()


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="Analyze a public GitHub repository",
    responses={
        400: {"description": "The provided URL is not a valid GitHub repository URL."},
        422: {"description": "The repository could not be cloned (not found, private, or unreachable)."},
    },
)
def analyze_repository(request: AnalyzeRequest) -> AnalyzeResponse:
    """Clone `request.repo_url`, scan it, detect its tech stack, build
    its graph, and cache the result as a new analysis session (Phase
    7A). The response's `analysis_id` can be passed to /explain,
    /explain/graph, or /components afterward to reuse this exact graph
    without re-cloning.

    A plain `def`, not `async def`, on purpose — same reasoning as
    before Phase 7A: cloning/scanning/parsing are all blocking,
    synchronous I/O, and FastAPI runs sync route handlers in a worker
    thread automatically, so this keeps the main event loop free.
    """
    result = analysis_service.create_analysis(request.repo_url)
    return analysis_service.to_analyze_response(result)
