"""Endpoint: clone a public GitHub repository, scan its contents, and
detect its languages, frameworks, and infrastructure tooling."""

import tempfile
from pathlib import Path

from fastapi import APIRouter

from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.services.framework_service import detect_frameworks
from app.services.git_service import clone_repository, parse_github_url
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
    succeeds or raises — framework/infrastructure detection reads manifest
    file contents, so it must happen before the `with` block exits.

    Note: this is a plain `def`, not `async def`, on purpose. Cloning,
    scanning, and reading manifest files are all blocking, synchronous
    I/O. FastAPI automatically runs sync route handlers in a worker
    thread, so this keeps the main event loop free to serve other
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

    return AnalyzeResponse(
        repository=repo,
        total_files=scan_result.total_files,
        languages=scan_result.languages,
        frameworks=frameworks,
        infrastructure=infrastructure,
        tree=scan_result.tree,
    )