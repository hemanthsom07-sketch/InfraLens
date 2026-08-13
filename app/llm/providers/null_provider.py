"""NullProvider: the default, dependency-free LLM provider.

Always reports itself unavailable and always raises LLMUnavailableError
from generate(). This is intentional, not a placeholder bug — it's what
lets the rest of the system (a later explanation engine) be built and
tested end-to-end against deterministic, template-based fallback
behavior before any real provider exists.
"""

from app.llm.exceptions import LLMUnavailableError
from app.llm.models import LLMRequest, LLMResponse
from app.llm.provider import LLMProvider


class NullProvider(LLMProvider):
    """An LLM provider that is never available."""

    @property
    def name(self) -> str:
        return "null"

    def is_available(self) -> bool:
        return False

    def generate(self, request: LLMRequest) -> LLMResponse:
        raise LLMUnavailableError(self.name)
