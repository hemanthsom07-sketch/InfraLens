"""Prompt Construction (Phase 5, Stage 5F).

Renders a Stage 5F GroundingContext into a deterministic, provider-
agnostic LLMRequest (Stage 5A). This is the final step before the
evidence leaves InfraLens's own code and goes to whichever LLMProvider
is configured (today: none — NullProvider is always unavailable, so
this code path exists but never actually runs against a real model).

ARCHITECTURAL RULE: this module builds a prompt from a GroundingContext
only. It does not gather evidence (Stage 5B), does not decide fallback
wording (Stage 5C), does not talk to GraphEngine, and does not know
which provider (if any) will eventually receive its output — LLMRequest
is the same provider-agnostic shape every provider already accepts.
"""

from app.explanation.evidence import EvidencePackage
from app.explanation.grounding import GroundingContext, build_grounding_context
from app.llm.models import LLMRequest

SYSTEM_PROMPT = (
    "You are InfraLens's infrastructure explanation assistant. "
    "You must only use the evidence provided in the user message below — "
    "never invent, assume, or infer any infrastructure relationship, "
    "component, or fact that is not explicitly present in that evidence. "
    "Facts listed under FACTS come from directly parsed configuration and "
    "may be stated with full confidence. "
    "Relationships listed under INFERRED were derived by InfraLens's own "
    "inference rules, not read directly from configuration — you must "
    "describe them as inferred or possible, never as certain, and must "
    "preserve their stated confidence level (high or heuristic) and basis "
    "when you mention them. Heuristic-confidence inferences are a weak "
    "signal; hedge them more strongly than high-confidence ones. "
    "If the evidence does not contain enough information to answer, say so "
    "explicitly rather than guessing."
)

_FACTS_HEADING = "FACTS (directly parsed — state as fact):"
_HIGH_CONFIDENCE_HEADING = "INFERRED, high confidence (InfraLens's own inference, not from configuration):"
_HEURISTIC_HEADING = "INFERRED, heuristic confidence (weak signal — hedge strongly):"
_OTHER_HEADING = "OTHER OBSERVATIONS (from deterministic graph analysis):"

_TASK_INSTRUCTION = "Using ONLY the evidence above, explain this to an infrastructure engineer."


def _render_section(heading: str, lines: list[str]) -> str | None:
    """One heading plus its bullet lines, or None if there's nothing to
    show — an empty section isn't rendered at all, so the LLM is never
    shown a heading with no content underneath it to (mis)interpret."""
    if not lines:
        return None
    bullets = "\n".join(f"- {line}" for line in lines)
    return f"{heading}\n{bullets}"


def _render_context(context: GroundingContext) -> str:
    """A deterministic text block from a GroundingContext: subjects,
    then only the non-empty sections, then the task instruction."""
    parts = [f"Subjects: {', '.join(context.subject_ids) or '(whole graph)'}"]

    for heading, lines in (
        (_FACTS_HEADING, context.facts),
        (_HIGH_CONFIDENCE_HEADING, context.high_confidence_inferences),
        (_HEURISTIC_HEADING, context.heuristic_inferences),
        (_OTHER_HEADING, context.other_observations),
    ):
        section = _render_section(heading, lines)
        if section is not None:
            parts.append(section)

    parts.append(_TASK_INSTRUCTION)
    return "\n\n".join(parts)


def build_prompt(package: EvidencePackage) -> LLMRequest:
    """EvidencePackage -> GroundingContext -> LLMRequest.

    Deterministic: the same EvidencePackage always produces the exact
    same LLMRequest (same system prompt, same rendered body), since
    GroundingContext's bucketing and this function's rendering are both
    pure functions of already-deterministic Stage 5B evidence.
    """
    context = build_grounding_context(package)
    return LLMRequest(system=SYSTEM_PROMPT, prompt=_render_context(context))
