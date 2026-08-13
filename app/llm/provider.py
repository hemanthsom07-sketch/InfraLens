"""LLMProvider: the contract every LLM provider implements.

This is the seam a real provider (explicitly out of scope for this
project right now) would implement later. Stage 5A defines the contract
and ships exactly one implementation — NullProvider — which is always
unavailable.
"""

from abc import ABC, abstractmethod

from app.llm.models import LLMRequest, LLMResponse


class LLMProvider(ABC):
    """Abstract base class for an LLM provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable identifier for this provider, e.g. 'null'."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this provider is currently usable (configured,
        reachable, etc.). Callers should check this — or be prepared to
        catch LLMUnavailableError from generate() — before relying on a
        real generated response."""

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a response for `request`.

        Raises LLMUnavailableError if this provider cannot currently
        service the request.
        """
