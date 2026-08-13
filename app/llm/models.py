"""Provider-agnostic request/response shapes for the LLM abstraction.

Deliberately minimal — prompt in, text out — so nothing here leaks a
specific vendor's API shape (message roles, tool-call blocks, etc.) into
the rest of the system. A concrete provider is free to translate an
LLMRequest into whatever shape its own SDK/API expects internally.
"""

from pydantic import BaseModel, Field


class LLMRequest(BaseModel):
    """A single generation request to an LLM provider."""

    prompt: str = Field(..., description="The fully-assembled prompt text to send to the provider.")
    system: str | None = Field(
        default=None,
        description="Optional system/instruction text, kept separate from `prompt` for providers that support it.",
    )
    max_tokens: int = Field(default=1024, description="Upper bound on generated output length.")


class LLMResponse(BaseModel):
    """A single generation result from an LLM provider."""

    text: str = Field(..., description="The provider's generated text.")
    provider_name: str = Field(..., description="Name of the provider that produced this response, e.g. 'null'.")
