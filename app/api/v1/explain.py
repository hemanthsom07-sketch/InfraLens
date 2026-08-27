"""Endpoints: explain a component, the relationship between two
components, or the whole infrastructure graph, for a given repository
or an already-created analysis session.

ARCHITECTURAL RULE: this router does not gather evidence, generate
wording, compute confidence, or talk to an LLM provider — it only
validates the request shape and calls app.services.explanation_service,
which is the single entry point into Stage 5D's ExplanationEngine.

Phase 7A: each request body now accepts EITHER `repo_url` (fresh
clone/scan/build, exactly as before) OR `analysis_id` (reuse an
already-built graph from a prior POST /analyze, no clone/scan/build at
all) — exactly one of the two, enforced by each request model's own
validator below, the same way ExplanationRequest already validates its
own node_id / source_id+target_id shape.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.models.explanation import ExplanationRequest, ExplanationResult
from app.services import explanation_service

router = APIRouter()


class ExplainAPIRequest(ExplanationRequest):
    """Request body for POST /explain: an analysis source (repo_url OR
    analysis_id) plus Stage 5A's existing node_id / source_id+target_id
    shape.

    Validation of the node_id / source_id+target_id shape is inherited
    unchanged from ExplanationRequest — not reimplemented here. This
    class adds its own, separate validator for the repo_url/analysis_id
    shape (Phase 7A); FastAPI surfaces a failure of either validator as
    its normal 422 response.
    """

    repo_url: str | None = Field(
        default=None,
        description="Public GitHub repository URL to analyze and explain. Mutually exclusive with analysis_id.",
    )
    analysis_id: str | None = Field(
        default=None,
        description=(
            "Id of an analysis session already created via POST /analyze (Phase 7A). "
            "Reuses that exact graph instead of triggering a fresh clone/scan. "
            "Mutually exclusive with repo_url."
        ),
    )

    @model_validator(mode="after")
    def _check_exactly_one_analysis_source(self) -> "ExplainAPIRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


class ExplainGraphAPIRequest(BaseModel):
    """Request body for POST /explain/graph: an analysis source
    (repo_url OR analysis_id) — there's no node/relationship to select,
    so no other fields apply."""

    repo_url: str | None = Field(
        default=None,
        description="Public GitHub repository URL to analyze and explain. Mutually exclusive with analysis_id.",
    )
    analysis_id: str | None = Field(
        default=None,
        description=(
            "Id of an analysis session already created via POST /analyze (Phase 7A). "
            "Reuses that exact graph instead of triggering a fresh clone/scan. "
            "Mutually exclusive with repo_url."
        ),
    )

    @model_validator(mode="after")
    def _check_exactly_one_analysis_source(self) -> "ExplainGraphAPIRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


@router.post(
    "/explain",
    response_model=ExplanationResult,
    summary="Explain a single component, or the relationship between two components",
    responses={
        404: {
            "description": (
                "The requested node id does not exist in the graph, or the given "
                "analysis_id is unknown/expired."
            )
        },
        422: {
            "description": (
                "Invalid request: neither node_id nor source_id/target_id given, or both "
                "given together; or neither repo_url nor analysis_id given, or both given "
                "together."
            )
        },
    },
)
def explain(request: ExplainAPIRequest) -> ExplanationResult:
    """Covers: component, dependencies, dependents, impact, and
    architecture/connections (node_id requests); relationship
    (source_id/target_id requests).

    A plain `def`, not `async def` — same reasoning as
    analyze_repository: cloning/scanning is blocking I/O, and FastAPI
    runs sync handlers in a worker thread automatically.
    """
    return explanation_service.explain(request.repo_url, request, analysis_id=request.analysis_id)


@router.post(
    "/explain/graph",
    response_model=ExplanationResult,
    summary="Explain the whole infrastructure graph",
    responses={
        404: {"description": "The given analysis_id is unknown/expired."},
        422: {"description": "Invalid request: neither repo_url nor analysis_id given, or both given together."},
    },
)
def explain_graph(request: ExplainGraphAPIRequest) -> ExplanationResult:
    """Covers: observations, cycles."""
    return explanation_service.explain_graph(request.repo_url, analysis_id=request.analysis_id)
