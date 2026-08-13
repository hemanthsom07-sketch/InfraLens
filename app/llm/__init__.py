"""LLM provider abstraction.

Defines a provider-agnostic contract (LLMProvider, LLMRequest, LLMResponse)
that the explanation engine (app/explanation/, a later stage) will depend
on, without depending on any specific vendor SDK.

Stage 5A ships exactly one provider — NullProvider (providers/null_provider.py)
— which is always unavailable and always raises LLMUnavailableError. This
keeps the whole system usable and fully testable via deterministic
fallback behavior before any real provider is wired in. A real provider
is explicitly out of scope for this stage.
"""
