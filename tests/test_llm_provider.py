"""Stage 5A: tests for the LLM provider abstraction
(app/llm/provider.py, app/llm/providers/null_provider.py,
app/llm/providers/registry.py).
"""

import pytest

from app.llm.exceptions import LLMUnavailableError
from app.llm.models import LLMRequest, LLMResponse
from app.llm.provider import LLMProvider
from app.llm.providers.null_provider import NullProvider
from app.llm.providers.registry import get_provider


# --- NullProvider ------------------------------------------------------------


def test_null_provider_is_an_llm_provider() -> None:
    provider = NullProvider()
    assert isinstance(provider, LLMProvider)


def test_null_provider_name() -> None:
    assert NullProvider().name == "null"


def test_null_provider_is_available_returns_false() -> None:
    assert NullProvider().is_available() is False


def test_null_provider_generate_raises_llm_unavailable_error() -> None:
    provider = NullProvider()
    request = LLMRequest(prompt="Explain this node.")

    with pytest.raises(LLMUnavailableError) as exc_info:
        provider.generate(request)

    assert exc_info.value.provider_name == "null"


def test_llm_unavailable_error_message_mentions_provider_name() -> None:
    error = LLMUnavailableError("null")
    assert "null" in str(error)


# --- registry ------------------------------------------------------------


def test_registry_returns_null_provider() -> None:
    provider = get_provider()
    assert isinstance(provider, NullProvider)


def test_registry_returned_provider_is_unavailable() -> None:
    provider = get_provider()
    assert provider.is_available() is False


# --- LLMRequest / LLMResponse models -----------------------------------------


def test_llm_request_defaults() -> None:
    request = LLMRequest(prompt="hello")
    assert request.prompt == "hello"
    assert request.system is None
    assert request.max_tokens == 1024


def test_llm_response_requires_provider_name() -> None:
    response = LLMResponse(text="some generated text", provider_name="null")
    assert response.text == "some generated text"
    assert response.provider_name == "null"
