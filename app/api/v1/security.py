"""Endpoint: deterministic, evidence-backed security findings for a
given repository or an already-created analysis session (Phase 7E).

ARCHITECTURAL RULE: this router does not decide what counts as a
finding — it only validates the request shape and calls
app.services.security_service, which delegates to app.security.rules'
fixed rule functions. No LLM is involved anywhere in this endpoint: the
deterministic rule engine decides every finding; this router just maps
each app.explanation.evidence.Observation (kind=SECURITY_FINDING) it
gets back into the public SecurityFinding/SecurityResponse shape below.

Accepts EITHER `repo_url` (fresh clone/scan/build) OR `analysis_id`
(reuse an already-built analysis from a prior POST /analyze) — the same
mutual-exclusivity validator pattern already established in
explain.py/components.py/impact.py/dependencies.py/graph_diagnostics.py.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator

from app.services import security_service

router = APIRouter()


class SecurityAPIRequest(BaseModel):
    """Request body for POST /security — just an analysis source, no
    other parameters: every rule scans the whole resolved analysis."""

    repo_url: str | None = Field(
        default=None, description="Public GitHub repository URL to analyze. Mutually exclusive with analysis_id."
    )
    analysis_id: str | None = Field(
        default=None,
        description=(
            "Id of an analysis session already created via POST /analyze (Phase 7A). Reuses "
            "that exact analysis instead of triggering a fresh clone/scan. Mutually exclusive "
            "with repo_url."
        ),
    )

    @model_validator(mode="after")
    def _check_exactly_one_analysis_source(self) -> "SecurityAPIRequest":
        if (self.repo_url is None) == (self.analysis_id is None):
            raise ValueError("Provide exactly one of repo_url or analysis_id.")
        return self


class SecurityFinding(BaseModel):
    """One deterministic security finding, built entirely from an
    Observation's own fields — see app.security.rules for exactly which
    parsed metadata field proves each rule's condition."""

    rule_id: str = Field(..., description="Which rule produced this finding, e.g. 'MUTABLE_IMAGE_TAG'.")
    severity: str = Field(..., description="One of: high, medium, low. Assigned by the fixed rule definition only.")
    title: str = Field(..., description="Short human-readable summary of the finding.")
    reason: str = Field(..., description="The concrete, factual reason this rule fired, citing the actual parsed value.")
    component_id: str = Field(..., description="The affected Component's id.")
    component_name: str = Field(..., description="The affected Component's name.")
    technology: str = Field(..., description="The affected Component's source technology, e.g. 'docker-compose'.")
    source_file: str | None = Field(default=None, description="The repo-relative file this finding's evidence came from.")


class SecurityResponse(BaseModel):
    """Response body for POST /security."""

    findings: list[SecurityFinding] = Field(default_factory=list)
    total_count: int = Field(..., description="len(findings).")
    counts_by_severity: dict[str, int] = Field(
        default_factory=dict, description="Finding count per severity value present, e.g. {'high': 2, 'medium': 1}."
    )


def _to_security_finding(observation) -> SecurityFinding:  # noqa: ANN001
    detail = observation.detail
    return SecurityFinding(
        rule_id=detail["rule_id"],
        severity=detail["severity"],
        title=detail["title"],
        reason=detail["reason"],
        component_id=observation.subject_id,
        component_name=detail["component_name"],
        technology=detail["technology"],
        source_file=detail.get("source_file"),
    )


@router.post(
    "/security",
    response_model=SecurityResponse,
    summary="Deterministic, evidence-backed security findings",
    responses={
        404: {"description": "The given analysis_id is unknown/expired."},
        422: {"description": "Invalid request: neither repo_url nor analysis_id given, or both given together."},
    },
)
def get_security_findings(request: SecurityAPIRequest) -> SecurityResponse:
    """A plain `def`, not `async def` — same reasoning as every other
    route in this API: resolving an analysis is blocking I/O in the
    fresh-clone case, and FastAPI runs sync handlers in a worker thread
    automatically."""
    observations = security_service.get_security_findings(
        analysis_id=request.analysis_id, repo_url=request.repo_url
    )
    findings = [_to_security_finding(observation) for observation in observations]

    counts_by_severity: dict[str, int] = {}
    for finding in findings:
        counts_by_severity[finding.severity] = counts_by_severity.get(finding.severity, 0) + 1

    return SecurityResponse(findings=findings, total_count=len(findings), counts_by_severity=counts_by_severity)
