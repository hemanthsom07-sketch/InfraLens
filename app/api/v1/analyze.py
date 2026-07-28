"""Phase 1 endpoint: clone a public GitHub repository and report on its contents."""

import tempfile
from pathlib import Path

from fastapi import APIRouter

from app.models.schemas import AnalyzeRequest, AnalyzeResponse
from app.services.git_service import clone_repository, parse_github_url
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
    """Clone `request.repo_url` into a temporary workspace, scan it, and return a summary.

    The temporary clone is always removed afterwards, whether the analysis
    succeeds or raises.

    Note: this is a plain `def`, not `async def`, on purpose. Cloning and
    scanning are blocking, synchronous I/O. FastAPI automatically runs
    sync route handlers in a worker thread, so this keeps the main event
    loop free to serve other requests. Declaring it `async def` while
    calling blocking code directly would instead stall the whole server
    for the duration of every clone.
    """
    owner, repo = parse_github_url(request.repo_url)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        destination = Path(tmp_dir)
        clone_repository(owner, repo, destination)
        total_files, languages, tree = scan_repository(destination)

    return AnalyzeResponse(
        repository=repo,
        total_files=total_files,
        languages=languages,
        tree=tree,
    )