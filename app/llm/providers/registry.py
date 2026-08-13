"""Provider registry: the single place that decides which LLMProvider
the rest of the system gets.

Currently always returns a NullProvider — there is no real provider to
select between yet. A later stage (explicitly out of scope for this
project right now) would extend get_provider() to read configuration and
choose a real provider when one is available, without any caller of
get_provider() needing to change.
"""

from app.llm.provider import LLMProvider
from app.llm.providers.null_provider import NullProvider


def get_provider() -> LLMProvider:
    """Return the LLMProvider the application should use.

    Stage 5A: always a fresh NullProvider instance.
    """
    return NullProvider()
