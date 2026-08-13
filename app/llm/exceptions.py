"""Custom exceptions for the LLM provider abstraction — same pattern as
app/exceptions.py and app/graph/exceptions.py.
"""


class LLMError(Exception):
    """Base class for all LLM provider errors."""


class LLMUnavailableError(LLMError):
    """Raised when generate() is called on a provider that isn't
    available (e.g. the NullProvider, or a real provider that isn't
    configured/reachable).

    Callers (the explanation engine, in a later stage) are expected to
    catch this and fall back to deterministic, template-based
    explanations rather than treating it as fatal.
    """

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name
        super().__init__(f"LLM provider '{provider_name}' is not available.")
