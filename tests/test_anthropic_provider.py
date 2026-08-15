"""Stage 5G: tests for app/llm/providers/anthropic_provider.py and the
registry selection logic in app/llm/providers/registry.py.

CRITICAL: no test in this file may make a real network call. The
autouse `_forbid_real_transport` fixture below replaces
anthropic_provider._default_transport (the real urllib-based
implementation) with a function that fails the test loudly if it's ever
invoked — so even a test that forgets to inject its own fake transport
fails immediately and obviously, instead of silently attempting a real
HTTPS request. Every test that reaches generate() must construct its
AnthropicProvider with an explicit transport=... of its own.

Dummy credentials only: "test-key-not-real" is never a valid Anthropic
API key and is used purely as a non-empty string for is_available()
checks.
"""

import pytest

from app.llm.providers import anthropic_provider
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.exceptions import LLMProviderError, LLMUnavailableError
from app.llm.models import LLMRequest
from app.llm.providers.null_provider import NullProvider
from app.llm.providers.registry import get_provider

_DUMMY_KEY = "test-key-not-real"


@pytest.fixture(autouse=True)
def _forbid_real_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> dict:
        raise AssertionError("real network transport must never be called in tests")

    monkeypatch.setattr(anthropic_provider, "_default_transport", _boom)


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with a clean environment for these three
    variables, regardless of what's set outside the test run."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INFRALENS_LLM_MODEL", raising=False)
    monkeypatch.delenv("INFRALENS_LLM_BASE_URL", raising=False)


def _fake_transport_returning(response: dict):
    def transport(base_url: str, api_key: str, payload: dict, timeout: float) -> dict:
        return response

    return transport


def _fake_transport_raising(exc: BaseException):
    def transport(base_url: str, api_key: str, payload: dict, timeout: float) -> dict:
        raise exc

    return transport


def _canned_success_response(text: str = "generated explanation text") -> dict:
    return {"content": [{"type": "text", "text": text}]}


# --- 1. availability with missing credentials --------------------------------


def test_is_available_false_with_no_api_key_env_var() -> None:
    provider = AnthropicProvider()
    assert provider.is_available() is False


def test_is_available_false_with_empty_string_api_key() -> None:
    provider = AnthropicProvider(api_key="")
    assert provider.is_available() is False


def test_generate_without_api_key_raises_llm_unavailable_error_and_never_calls_transport() -> None:
    calls = []
    provider = AnthropicProvider(transport=lambda *a, **kw: calls.append(1) or {})

    with pytest.raises(LLMUnavailableError):
        provider.generate(LLMRequest(prompt="explain this"))

    assert calls == []  # generate() must not attempt a call at all when unavailable


# --- 2. availability with configured credentials -----------------------------


def test_is_available_true_with_api_key_argument() -> None:
    provider = AnthropicProvider(api_key=_DUMMY_KEY)
    assert provider.is_available() is True


def test_is_available_true_with_api_key_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _DUMMY_KEY)
    provider = AnthropicProvider()
    assert provider.is_available() is True


def test_provider_name_is_anthropic() -> None:
    assert AnthropicProvider(api_key=_DUMMY_KEY).name == "anthropic"


# --- 3. request translation ---------------------------------------------------


def test_request_translation_includes_system_and_prompt_and_max_tokens() -> None:
    captured = {}

    def capturing_transport(base_url, api_key, payload, timeout):
        captured.update(payload)
        return _canned_success_response()

    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=capturing_transport)
    provider.generate(LLMRequest(system="be helpful", prompt="explain backend", max_tokens=256))

    assert captured["system"] == "be helpful"
    assert captured["messages"] == [{"role": "user", "content": "explain backend"}]
    assert captured["max_tokens"] == 256


def test_request_translation_omits_system_when_none() -> None:
    captured = {}

    def capturing_transport(base_url, api_key, payload, timeout):
        captured.update(payload)
        return _canned_success_response()

    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=capturing_transport)
    provider.generate(LLMRequest(prompt="explain backend"))

    assert "system" not in captured


def test_request_translation_uses_configured_model() -> None:
    captured = {}

    def capturing_transport(base_url, api_key, payload, timeout):
        captured.update(payload)
        return _canned_success_response()

    provider = AnthropicProvider(api_key=_DUMMY_KEY, model="a-specific-model", transport=capturing_transport)
    provider.generate(LLMRequest(prompt="explain backend"))

    assert captured["model"] == "a-specific-model"


def test_request_translation_passes_base_url_and_api_key_to_transport() -> None:
    seen = {}

    def capturing_transport(base_url, api_key, payload, timeout):
        seen["base_url"] = base_url
        seen["api_key"] = api_key
        return _canned_success_response()

    provider = AnthropicProvider(
        api_key=_DUMMY_KEY, base_url="https://example-test-base.invalid", transport=capturing_transport
    )
    provider.generate(LLMRequest(prompt="explain backend"))

    assert seen["base_url"] == "https://example-test-base.invalid"
    assert seen["api_key"] == _DUMMY_KEY


# --- 4. successful mocked generation -----------------------------------------


def test_successful_generation_returns_llm_response() -> None:
    provider = AnthropicProvider(
        api_key=_DUMMY_KEY, transport=_fake_transport_returning(_canned_success_response("hello from the model"))
    )
    response = provider.generate(LLMRequest(prompt="explain backend"))

    assert response.text == "hello from the model"
    assert response.provider_name == "anthropic"


# --- 5. provider API / network failure ---------------------------------------


def test_network_error_becomes_llm_provider_error() -> None:
    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=_fake_transport_raising(TimeoutError("boom")))

    with pytest.raises(LLMProviderError):
        provider.generate(LLMRequest(prompt="explain backend"))


def test_provider_error_is_also_an_llm_unavailable_error() -> None:
    """The critical compatibility guarantee: existing callers written
    against LLMUnavailableError (Stage 5D's ExplanationEngine) must keep
    catching real-provider failures without any change."""
    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=_fake_transport_raising(OSError("network down")))

    with pytest.raises(LLMUnavailableError):
        provider.generate(LLMRequest(prompt="explain backend"))


def test_provider_error_message_does_not_leak_original_exception_text() -> None:
    """The original exception's message could echo a response body or
    other detail — only its type name should surface."""
    provider = AnthropicProvider(
        api_key=_DUMMY_KEY, transport=_fake_transport_raising(RuntimeError("super secret internal detail"))
    )

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate(LLMRequest(prompt="explain backend"))

    assert "super secret internal detail" not in str(exc_info.value)
    assert "RuntimeError" in str(exc_info.value)


def test_provider_error_never_contains_api_key() -> None:
    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=_fake_transport_raising(OSError("boom")))

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate(LLMRequest(prompt="explain backend"))

    assert _DUMMY_KEY not in str(exc_info.value)


# --- 6. malformed / unexpected provider response -----------------------------


def test_missing_content_key_becomes_llm_provider_error() -> None:
    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=_fake_transport_returning({}))

    with pytest.raises(LLMProviderError):
        provider.generate(LLMRequest(prompt="explain backend"))


def test_empty_content_list_becomes_llm_provider_error() -> None:
    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=_fake_transport_returning({"content": []}))

    with pytest.raises(LLMProviderError):
        provider.generate(LLMRequest(prompt="explain backend"))


def test_missing_text_field_becomes_llm_provider_error() -> None:
    provider = AnthropicProvider(
        api_key=_DUMMY_KEY, transport=_fake_transport_returning({"content": [{"type": "text"}]})
    )

    with pytest.raises(LLMProviderError):
        provider.generate(LLMRequest(prompt="explain backend"))


def test_non_string_text_field_becomes_llm_provider_error() -> None:
    provider = AnthropicProvider(
        api_key=_DUMMY_KEY, transport=_fake_transport_returning({"content": [{"type": "text", "text": 12345}]})
    )

    with pytest.raises(LLMProviderError):
        provider.generate(LLMRequest(prompt="explain backend"))


def test_malformed_response_does_not_raise_an_unrelated_exception_type() -> None:
    """A malformed response must map to LLMProviderError, never let a
    raw KeyError/IndexError/TypeError escape this module."""
    provider = AnthropicProvider(api_key=_DUMMY_KEY, transport=_fake_transport_returning({"content": "not-a-list"}))

    try:
        provider.generate(LLMRequest(prompt="explain backend"))
        raise AssertionError("expected LLMProviderError to be raised")
    except LLMProviderError:
        pass


# --- 7/8. registry selection --------------------------------------------------


def test_registry_returns_null_provider_when_unconfigured() -> None:
    provider = get_provider()
    assert isinstance(provider, NullProvider)


def test_registry_returns_anthropic_provider_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _DUMMY_KEY)
    provider = get_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider.is_available() is True


def test_registry_falls_back_to_null_provider_with_empty_string_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    provider = get_provider()
    assert isinstance(provider, NullProvider)


# --- 9. no real network calls -------------------------------------------------


def test_default_transport_is_never_invoked_in_this_test_run() -> None:
    """Confirms the autouse guard fixture is actually wired up: calling
    the real default transport (via the module attribute the fixture
    replaced) must raise, proving no test could have silently reached
    the real implementation."""
    with pytest.raises(AssertionError):
        anthropic_provider._default_transport("https://api.anthropic.com", _DUMMY_KEY, {}, 30.0)
