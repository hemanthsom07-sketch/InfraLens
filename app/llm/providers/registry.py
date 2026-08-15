"""Provider registry: the single place that decides which LLMProvider
the rest of the system gets.

Selects a real provider only when one is actually configured
(ANTHROPIC_API_KEY set); otherwise falls back to NullProvider, exactly
as before this stage. No caller of get_provider() needs to change either
way — the function's signature and contract are unchanged.
"""

from app.llm.provider import LLMProvider
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.null_provider import NullProvider


def get_provider() -> LLMProvider:
    """Return the LLMProvider the application should use.

    A fresh AnthropicProvider if ANTHROPIC_API_KEY (read via
    AnthropicProvider's own os.environ lookup) is set and non-empty;
    otherwise a fresh NullProvider. This check is local/synchronous
    (AnthropicProvider.is_available() never makes a network call), so
    calling this is always cheap and side-effect-free.

    Default behavior (no environment configured at all) is unchanged
    from every prior stage: NullProvider, so the deterministic fallback
    path remains what every caller gets unless a real provider has been
    explicitly configured.
    """
    provider = AnthropicProvider()
    if provider.is_available():
        return provider
    return NullProvider()
