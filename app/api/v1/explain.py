"""Endpoints: explain a component, the relationship between two
components, or the whole infrastructure graph, for a given repository.

ARCHITECTURAL RULE: this router does not gather evidence, generate
wording, compute confidence, or talk to an LLM provider — it only
validates the request shape and calls app.services.explanation_service,
which is the single entry point into Stage 5D's ExplanationEngine.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.models.explanation import ExplanationRequest, ExplanationResult
from app.services import explanation_service

router = APIRouter()


class ExplainAPIRequest(ExplanationRequest):
    """Request body for POST /explain: repository context plus Stage 5A's
    existing node_id / source_id+target_id shape.

    Validation (exactly one of node_id, or source_id+target_id together)
    is inherited unchanged from ExplanationRequest — not reimplemented
    here. FastAPI surfaces a failure of that validator as its normal 422
    response.
    """

    repo_url: str = Field(..., description="Public GitHub repository URL to analyze and explain.")


class ExplainGraphAPIRequest(BaseModel):
    """Request body for POST /explain/graph: just repository context —
    there's no node/relationship to select, so no other fields apply."""

    repo_url: str = Field(..., description="Public GitHub repository URL to analyze and explain.")


@router.post(
    "/explain",
    response_model=ExplanationResult,
    summary="Explain a single component, or the relationship between two components",
    responses={
        404: {"description": "The requested node id does not exist in the graph."},
        422: {
            "description": (
                "Invalid request: neither node_id nor source_id/target_id given, "
                "or both given together."
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
    return explanation_service.explain(request.repo_url, request)


@router.post(
    "/explain/graph",
    response_model=ExplanationResult,
    summary="Explain the whole infrastructure graph",
)
def explain_graph(request: ExplainGraphAPIRequest) -> ExplanationResult:
    """Covers: observations, cycles."""
    return explanation_service.explain_graph(request.repo_url)
