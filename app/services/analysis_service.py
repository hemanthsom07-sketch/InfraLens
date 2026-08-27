"""Shared Analysis Service (Phase 7A).

Owns the clone -> scan -> detect -> parse -> resolve -> infer -> build
pipeline that api/v1/analyze.py, explanation_service.py, and
component_lookup_service.py each used to run independently, and
introduces the analysis-session concept: every completed analysis is
assigned a unique analysis_id and cached in an in-memory AnalysisStore
(app.services.analysis_store), so a caller can reuse an already-built
graph instead of triggering a fresh clone/scan/parse/build.

ARCHITECTURAL RULE: this module is the ONLY place that owns the
clone-to-graph pipeline now. app.services.explanation_service and
app.services.component_lookup_service no longer clone or scan
independently — both resolve their GraphEngine through
get_or_create_analysis() below. GraphEngine itself remains the sole
graph abstraction; this module never imports networkx or touches
app.graph.core/app.graph.algorithms directly, only
app.services.graph_service.build_graph() (Phase 4's existing public
entry point), exactly like the code it replaces already did.

CACHING SCOPE (explicitly bounded for this phase): in-memory only, one
process, no persistence across restarts, no auth/multi-user isolation
beyond analysis_id itself, no distributed cache. See
app.services.analysis_store.AnalysisStore for the eviction policy.
"""

import tempfile
import time
import uuid
from pathlib import Path

from app.exceptions import AnalysisNotFoundError
from app.models.schemas import AnalyzeResponse
from app.services.analysis_store import AnalysisResult, AnalysisStore
from app.services.framework_service import detect_frameworks
from app.services.git_service import clone_repository, parse_github_url
from app.services.graph_service import build_graph
from app.services.ikm_service import build_infrastructure_model
from app.services.infrastructure_service import detect_infrastructure
from app.services.scanner_service import scan_repository

# Defaults for Phase 7A's bounded in-memory cache — no config system
# exists yet for these, so they're plain module constants, easy to find
# and adjust. 100 entries / 30 minutes is generous for local/demo use
# without being unbounded; revisit once real usage patterns exist.
_DEFAULT_MAX_SIZE = 100
_DEFAULT_TTL_SECONDS = 1800.0

# Module-level singleton store. Tests that need to control eviction
# behavior deterministically construct their own AnalysisStore (with a
# small max_size/ttl_seconds and/or an injected clock) and monkeypatch
# this module attribute directly — the same seam-replacement pattern
# already used throughout this codebase's test suite.
_store = AnalysisStore(max_size=_DEFAULT_MAX_SIZE, ttl_seconds=_DEFAULT_TTL_SECONDS)


def run_analysis(repo_url: str) -> AnalysisResult:
    """Clone, scan, detect, parse, and build the graph for `repo_url`.

    Pure with respect to the cache — this function never reads from or
    writes to the AnalysisStore, so it's the one seam a test can
    monkeypatch to skip real cloning while still exercising the actual
    create/get/store logic in create_analysis() below.

    Raises InvalidRepositoryURLError / RepositoryCloneError
    (app.exceptions), unchanged, for a bad or unreachable repository
    URL. Any other exception (a parser bug, a graph-build failure)
    propagates unchanged too — this function either returns a fully
    complete AnalysisResult or raises; it never returns a partial one.
    """
    owner, repo = parse_github_url(repo_url)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        destination = Path(tmp_dir)
        clone_repository(owner, repo, destination)
        scan_result = scan_repository(destination)
        frameworks = detect_frameworks(scan_result.file_paths)
        infrastructure = detect_infrastructure(scan_result.file_paths)
        infrastructure_model = build_infrastructure_model(scan_result.file_paths, destination)
        graph_engine = build_graph(infrastructure_model)

    return AnalysisResult(
        analysis_id=uuid.uuid4().hex,
        repository=repo,
        total_files=scan_result.total_files,
        languages=scan_result.languages,
        frameworks=frameworks,
        infrastructure=infrastructure,
        infrastructure_model=infrastructure_model,
        graph_engine=graph_engine,
        tree=scan_result.tree,
        created_at=time.time(),
    )


def create_analysis(repo_url: str) -> AnalysisResult:
    """run_analysis() + cache the result under its new analysis_id.

    The result is only ever written to the store after run_analysis()
    has fully returned — if run_analysis() raises (bad URL, clone
    failure, or any other error), this function never reaches the
    store.set() call, so a failed analysis can never leave a corrupt or
    partial cache entry behind.
    """
    result = run_analysis(repo_url)
    _store.set(result.analysis_id, result)
    return result


def get_analysis(analysis_id: str) -> AnalysisResult:
    """The cached AnalysisResult for `analysis_id`.

    Raises AnalysisNotFoundError if no analysis exists for that id —
    either it was never created, or it has expired/been evicted from
    the in-memory store (see app.services.analysis_store.AnalysisStore).
    """
    result = _store.get(analysis_id)
    if result is None:
        raise AnalysisNotFoundError(analysis_id)
    return result


def get_or_create_analysis(*, analysis_id: str | None, repo_url: str | None) -> AnalysisResult:
    """The one resolution entry point every analysis-dependent service
    function should call:

    - `analysis_id` given -> look up the cached AnalysisResult (raises
      AnalysisNotFoundError if unknown/expired). The graph is NOT
      rebuilt in this path — this is the whole point of Phase 7A.
    - `repo_url` given (and no analysis_id) -> create and cache a fresh
      analysis, exactly like POST /analyze does.

    Exactly one of the two is expected to be given. API-layer request
    models (app.api.v1.explain.ExplainAPIRequest,
    app.api.v1.components.ComponentListRequest, etc.) are responsible
    for rejecting "neither" or "both" before a request ever reaches
    this function; this function raises ValueError for that case as a
    defensive fallback for any other caller, but the intended fast-fail
    for API requests is the request model's own validator (a 422, not a
    500 from this function ever needing to run at all).
    """
    if analysis_id is not None:
        return get_analysis(analysis_id)
    if repo_url is not None:
        return create_analysis(repo_url)
    raise ValueError("Either analysis_id or repo_url must be provided.")


def to_analyze_response(result: AnalysisResult) -> AnalyzeResponse:
    """AnalysisResult -> the existing AnalyzeResponse wire shape,
    reusing GraphEngine.to_model() exactly as api/v1/analyze.py always
    did — this function only adds analysis_id to that same shape."""
    return AnalyzeResponse(
        analysis_id=result.analysis_id,
        repository=result.repository,
        total_files=result.total_files,
        languages=result.languages,
        frameworks=result.frameworks,
        infrastructure=result.infrastructure,
        infrastructure_model=result.infrastructure_model,
        graph=result.graph_engine.to_model(),
        tree=result.tree,
    )
