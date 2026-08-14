"""Stage 5F: proves app/explanation/engine.py delegates LLM prompt
construction to app.explanation.prompts.build_prompt rather than
building its own prompt (the old Stage 5D placeholder this replaces).

Deliberately a separate file rather than additions to
tests/test_explanation_engine.py, so Stage 5D's existing test file is
left completely untouched by this stage.
"""

from app.explanation.engine import ExplanationEngine
from app.explanation.evidence import build_node_evidence
from app.explanation.prompts import SYSTEM_PROMPT, build_prompt
from app.graph.engine import GraphEngine
from app.llm.models import LLMRequest, LLMResponse
from app.llm.provider import LLMProvider
from app.models.explanation import ExplanationRequest
from app.models.ikm import Component, InfrastructureModel, Relationship


def _main_engine() -> GraphEngine:
    components = [
        Component(
            id="backend", name="backend", type="service", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
        Component(
            id="db", name="db", type="database", technology="docker-compose",
            metadata={"source_file": "docker-compose.yml"},
        ),
    ]
    relationships = [Relationship(source="backend", target="db", relationship_type="depends_on")]
    model = InfrastructureModel(components=components, relationships=relationships)
    return GraphEngine.from_infrastructure_model(model, infer=True)


class _CapturingProvider(LLMProvider):
    """Available, and records exactly the LLMRequest it was given."""

    def __init__(self) -> None:
        self.received_requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return "capturing"

    def is_available(self) -> bool:
        return True

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.received_requests.append(request)
        return LLMResponse(text="captured", provider_name=self.name)


def test_engine_sends_a_stage_5f_prompt_to_the_provider() -> None:
    graph = _main_engine()
    provider = _CapturingProvider()
    engine = ExplanationEngine(graph, provider=provider)

    engine.explain(ExplanationRequest(node_id="backend"))

    assert len(provider.received_requests) == 1
    assert provider.received_requests[0].system == SYSTEM_PROMPT


def test_engine_prompt_matches_stage_5f_build_prompt_exactly() -> None:
    """The engine must not build its own version of the prompt — the
    LLMRequest it sends must be byte-for-byte what prompts.build_prompt
    produces for the same evidence."""
    graph = _main_engine()
    provider = _CapturingProvider()
    engine = ExplanationEngine(graph, provider=provider)

    engine.explain(ExplanationRequest(node_id="backend"))

    expected_request = build_prompt(build_node_evidence(graph, "backend"))
    assert provider.received_requests[0] == expected_request


def test_engine_delegates_graph_level_prompt_too() -> None:
    from app.explanation.evidence import build_graph_evidence

    graph = _main_engine()
    provider = _CapturingProvider()
    engine = ExplanationEngine(graph, provider=provider)

    engine.explain_graph()

    expected_request = build_prompt(build_graph_evidence(graph))
    assert provider.received_requests[0] == expected_request


def test_engine_still_falls_back_to_template_when_no_provider_available() -> None:
    """Stage 5F must not change Stage 5D's fallback behavior — confirms
    the swap preserved existing engine behavior, not just added a new
    prompt."""
    engine = ExplanationEngine(_main_engine())  # default: NullProvider, unavailable
    result = engine.explain(ExplanationRequest(node_id="backend"))
    assert result.generation_method == "template"
    assert result.provider_name is None
