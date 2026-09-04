"""Application-level entry point for deterministic security findings
(Phase 7E; extended by Phase 9).

AUDIT SUMMARY (see app.security.rules module docstring for the full
detail): inspection of every parser's actual captured
Component.metadata found real support for exactly 9 metadata-only rules
(Phase 7E: 5 rules across Compose/Docker/Kubernetes; Phase 7G: 4
Kubernetes-specific rules) plus, as of Phase 9, one graph-reachability
rule that genuinely needs GraphEngine and therefore doesn't fit the
metadata-only run_security_rules() interface those 9 share.

Reuses app.services.analysis_service.get_or_create_analysis() exactly
as-is (Phase 7A) to resolve an AnalysisResult. This module now consults
BOTH analysis.infrastructure_model (for the 9 metadata-only rules,
completely independent of GraphEngine, exactly as before) AND
analysis.graph_engine (for Phase 9's attack-surface check, via
GraphEngine's own public shortest_path() method only -- never
networkx, never graph internals, the same discipline
app.services.impact_service/dependency_service/graph_diagnostics_service
already established for exactly this reason).

Phase 9 is deliberately NOT forced into run_security_rules()'s
metadata-only interface: app.security.attack_surface.check_attack_surface()
is run as a separate, parallel check and its results are merged with
run_security_rules()'s output here, then re-sorted once as one
combined, deterministic list -- app.security.rules.py itself is
completely unmodified.

Contains no LLM reasoning of any kind: every finding is produced by
either app.security.rules' fixed, deterministic rule functions, or
app.security.attack_surface's fixed, deterministic GraphEngine
reachability check. If a future phase adds narration, that belongs to
the explanation layer consuming these same Observation objects
(kind=SECURITY_FINDING) -- not here.
"""

from app.explanation.evidence import Observation
from app.security.attack_surface import check_attack_surface
from app.security.rules import run_security_rules
from app.services import analysis_service


def get_security_findings(*, analysis_id: str | None = None, repo_url: str | None = None) -> list[Observation]:
    """Every deterministic security finding for the resolved analysis,
    as Observation objects (kind=SECURITY_FINDING) -- the 9 existing
    metadata-only rules (Phase 7E/7G) plus Phase 9's GraphEngine-based
    attack-surface findings, merged into one deterministically-ordered
    list (sorted by (subject_id, rule_id), the same convention
    app.security.rules.run_security_rules() already uses on its own
    output -- applied once more here across the combined set, so the
    final order is stable regardless of which check found what).

    `analysis_id`/`repo_url` follow analysis_service.get_or_create_analysis()'s
    resolution rule: give `analysis_id` to reuse an already-built
    analysis, or `repo_url` to build a fresh one.

    Raises AnalysisNotFoundError (unknown/expired analysis_id) or
    InvalidRepositoryURLError/RepositoryCloneError (bad/unreachable
    repo_url) -- unchanged from their respective owning layers. There is
    no NodeNotFoundError path here: both checks scan every component in
    the resolved analysis, neither takes a node id from the caller.
    """
    analysis = analysis_service.get_or_create_analysis(analysis_id=analysis_id, repo_url=repo_url)

    metadata_findings = run_security_rules(analysis.infrastructure_model)
    attack_surface_findings = check_attack_surface(analysis.infrastructure_model.components, analysis.graph_engine)

    combined = [*metadata_findings, *attack_surface_findings]
    combined.sort(key=lambda observation: (observation.subject_id or "", observation.detail["rule_id"]))
    return combined
