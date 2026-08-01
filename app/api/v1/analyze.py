"""Endpoint: clone a public GitHub repository, scan its contents, and
detect its languages, frameworks, infrastructure tooling, structured
Infrastructure Knowledge Model, and queryable dependency graph."""

import tempfile
from pathlib import Path

from fastapi import APIRouter

from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.services.framework_service import detect_frameworks
from app.services.git_service import clone_repository, parse_github_url
from app.services.graph_service import build_graph
from app.services.ikm_service import build_infrastructure_model
from app.services.infrastructure_service import detect_infrastructure
from app.services.scanner_service import scan_repository

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
    """Clone `request.repo_url` into a temporary workspace, scan it, detect
    its tech stack, and return a summary.

    The temporary clone is always removed afterwards, whether the analysis
    succeeds or raises — framework/infrastructure detection and IKM
    parsing all read file contents, so they must happen before the `with`
    block exits. (Building the graph itself doesn't need file access —
    it only reads Component metadata already in memory — but it's kept
    inside the same block for simplicity, since there's no benefit to
    moving it outside.)

    Note: this is a plain `def`, not `async def`, on purpose. Cloning,
    scanning, and reading manifest/infrastructure files are all blocking,
    synchronous I/O. FastAPI automatically runs sync route handlers in a
    worker thread, so this keeps the main event loop free to serve other
    requests. Declaring it `async def` while calling blocking code
    directly would instead stall the whole server for the duration of
    every clone.
    """
    owner, repo = parse_github_url(request.repo_url)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        destination = Path(tmp_dir)
        clone_repository(owner, repo, destination)
        scan_result = scan_repository(destination)
        frameworks = detect_frameworks(scan_result.file_paths)
        infrastructure = detect_infrastructure(scan_result.file_paths)
        infrastructure_model = build_infrastructure_model(scan_result.file_paths, destination)
        graph_engine = build_graph(infrastructure_model)

    return AnalyzeResponse(
        repository=repo,
        total_files=scan_result.total_files,
        languages=scan_result.languages,
        frameworks=frameworks,
        infrastructure=infrastructure,
        infrastructure_model=infrastructure_model,
        graph=graph_engine.to_model(),
        tree=scan_result.tree,
    )
