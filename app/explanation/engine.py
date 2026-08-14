"""ExplanationEngine (Phase 5, Stage 5D).

Orchestrates the layers already built in Stages 5A-5C into one entry
point:

    GraphEngine + ExplanationRequest
        -> EvidencePackage             (Stage 5B: app.explanation.evidence)
        -> LLM provider, if available  (Stage 5A: app.llm)
        -> deterministic fallback      (Stage 5C: app.explanation.fallback)
        -> ExplanationResult           (Stage 5A: app.models.explanation)

ARCHITECTURAL RULE: this module contains NO evidence-gathering logic and
NO explanation wording of its own. It only calls into the modules that
already own those responsibilities, and decides which path (LLM vs.
fallback) a given request takes. It never imports networkx and never
reaches into GraphEngine's internals — every graph fact it touches comes
through the Stage 5B evidence functions, which are themselves
GraphEngine-public-API-only.

Confidence is never invented here either: whichever path is taken (LLM
or template), the Confidence value comes from Stage 5C's own provenance
based banding (app.explanation.fallback.generate_fallback_explanation),
so an LLM-generated explanation is held to the same honesty standard as
a template one.
"""

from app.explanation.evidence import EvidencePackage, build_graph_evidence, build_node_evidence, build_relationship_evidence
from app.explanation.fallback import generate_fallback_explanation
from app.graph.engine import GraphEngine
from app.llm.exceptions import LLMUnavailableError
from app.llm.models import LLMRequest
from app.llm.provider import LLMProvider
from app.llm.providers.registry import get_provider
from app.models.explanation import ExplanationRequest, ExplanationResult


def _build_evidence(graph_engine: GraphEngine, request: ExplanationRequest) -> EvidencePackage:
    """Delegate entirely to Stage 5B. This function only decides WHICH
    Stage 5B builder to call, based on the request's shape — it does not
    gather any evidence itself.

    NodeNotFoundError propagates unchanged from the Stage 5B builders if
    the requested node id(s) don't exist in `graph_engine`.
    """
    if request.node_id is not None:
        return build_node_evidence(graph_engine, request.node_id)
    return build_relationship_evidence(graph_engine, request.source_id, request.target_id)


def _build_llm_prompt(package: EvidencePackage) -> str:
    """A minimal, structural prompt built from the evidence package.

    This is intentionally bare — proper prompt engineering and grounding
    against the evidence (Stage 5F) is explicitly out of scope for this
    stage. It exists only so the LLM branch in _resolve() is a real,
    exercisable code path today; it is not the final prompting strategy.
    """
    lines = [f"subjects: {', '.join(package.subject_ids) or '(whole graph)'}"]
    lines.extend(observation.model_dump_json() for observation in package.observations)
    return "\n".join(lines)


class ExplanationEngine:
    """Coordinates evidence (5B), an LLM provider (5A), and deterministic
    fallback wording (5C) for one bound GraphEngine.

    If `provider` isn't given, Stage 5A's registry is used — currently
    always a NullProvider, so every explanation takes the deterministic
    fallback path (generation_method="template", provider_name=None)
    until a real provider exists (Stage 5G, out of scope here).
    """

    def __init__(self, graph_engine: GraphEngine, provider: LLMProvider | None = None) -> None:
        self._graph_engine = graph_engine
        self._provider = provider if provider is not None else get_provider()

    def explain(self, request: ExplanationRequest) -> ExplanationResult:
        """Explain a single node, or the relationship between two nodes,
        per `request`'s shape (app.models.explanation.ExplanationRequest).

        Covers: component, dependencies, dependents, impact, and
        architecture/connections (node requests); relationship
        (source_id/target_id requests).

        Raises NodeNotFoundError, propagated from Stage 5B, for an
        unknown node id.
        """
        package = _build_evidence(self._graph_engine, request)
        return self._resolve(package)

    def explain_graph(self) -> ExplanationResult:
        """Explain the whole graph bound to this engine.

        Covers: observations, cycles.
        """
        package = build_graph_evidence(self._graph_engine)
        return self._resolve(package)

    def _resolve(self, package: EvidencePackage) -> ExplanationResult:
        """EvidencePackage -> LLM provider if available -> deterministic
        fallback -> ExplanationResult.

        The fallback result is always computed, regardless of which path
        is ultimately returned: it's the return value on the fallback
        path, and the source of confidence/evidence on the LLM path.
        Either way, Stage 5C's provenance based confidence banding is
        never bypassed or re-derived here.
        """
        fallback_result = generate_fallback_explanation(package)

        if self._provider.is_available():
            try:
                llm_response = self._provider.generate(LLMRequest(prompt=_build_llm_prompt(package)))
                return ExplanationResult(
                    explanation=llm_response.text,
                    confidence=fallback_result.confidence,
                    generation_method="llm",
                    provider_name=llm_response.provider_name,
                    evidence=fallback_result.evidence,
                )
            except LLMUnavailableError:
                pass  # fall through to the deterministic fallback below

        return fallback_result
