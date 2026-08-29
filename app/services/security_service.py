"""Application-level entry point for deterministic security findings
(Phase 7E).

AUDIT SUMMARY (see app.security.rules module docstring for the full
detail): inspection of every parser's actual captured
Component.metadata found real support for exactly 5 rules —
image-tag mutability, hardcoded secret-like env vars, a Docker socket
bind-mount, a known database image publishing a port, and a sensitive
administrative port published — spanning Compose, Docker, and
Kubernetes. Kubernetes' TLS/Service-type/Secret-data/privileged fields
and every Terraform resource-body attribute are NOT captured by the
current parsers at all, so no rule for any of those was invented.

Reuses app.services.analysis_service.get_or_create_analysis() exactly
as-is (Phase 7A) to resolve an AnalysisResult, then reads
analysis.infrastructure_model directly — the same already-parsed
InfrastructureModel every parser produces, completely independent of
GraphEngine. This module never touches GraphEngine, never imports
networkx, and never duplicates any parser's own extraction logic — it
only reads Component.metadata fields the parsers already populated.

Deliberately NOT graph-aware: none of the 5 rules need relationship/
traversal information, only per-component metadata, so there's no
reason for this module (or app.security.rules) to resolve a GraphEngine
at all — resolving analysis.infrastructure_model directly is both
simpler and a stronger architectural guarantee than routing through
GraphEngine.to_model() would be.

Contains no LLM reasoning of any kind: every finding is produced by
app.security.rules' fixed, deterministic rule functions. If a future
phase adds narration, that belongs to the explanation layer consuming
these same Observation objects (kind=SECURITY_FINDING) — not here.
"""

from app.explanation.evidence import Observation
from app.security.rules import run_security_rules
from app.services import analysis_service


def get_security_findings(*, analysis_id: str | None = None, repo_url: str | None = None) -> list[Observation]:
    """Every deterministic security finding for the resolved analysis,
    as Observation objects (kind=SECURITY_FINDING) — deterministically
    ordered (see app.security.rules.run_security_rules()).

    `analysis_id`/`repo_url` follow analysis_service.get_or_create_analysis()'s
    resolution rule: give `analysis_id` to reuse an already-built
    analysis, or `repo_url` to build a fresh one.

    Raises AnalysisNotFoundError (unknown/expired analysis_id) or
    InvalidRepositoryURLError/RepositoryCloneError (bad/unreachable
    repo_url) — unchanged from their respective owning layers. There is
    no NodeNotFoundError path here: this scans every component in the
    resolved analysis, it does not take a node id.
    """
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)
    return run_security_rules(analysis.infrastructure_model)
