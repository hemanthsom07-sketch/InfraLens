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


class LLMProviderError(LLMUnavailableError):
    """A real provider WAS configured and reachable enough to attempt a
    call, but the call itself failed — a network error, a non-2xx HTTP
    status, a response that couldn't be parsed as JSON, or a JSON body
    missing the fields a response is expected to have.

    Deliberately a subclass of LLMUnavailableError, not a sibling: every
    existing `except LLMUnavailableError` (currently just
    app.explanation.engine.ExplanationEngine._resolve) keeps catching
    this without any change, so a real provider's failures fall back to
    the deterministic template path exactly like an unconfigured
    NullProvider's do. This subclass exists only to let a provider
    record which underlying condition caused the failure, for whoever
    wants that detail (e.g. server-side logging) without it needing to
    change how callers already handle LLM unavailability.

    Never include the provider's raw response body or any credential in
    the message — only what's needed to identify the failure kind.
    """

    def __init__(self, provider_name: str, reason: str) -> None:
        self.provider_name = provider_name
        self.reason = reason
        # Deliberately calls LLMError's own base (Exception), not
        # LLMUnavailableError.__init__ — this sets a message that also
        # names the failure kind, while still never including secrets or
        # raw response bodies. isinstance/except compatibility with
        # LLMUnavailableError comes from the class hierarchy, not from
        # which __init__ ran.
        Exception.__init__(self, f"LLM provider '{provider_name}' request failed: {reason}")
