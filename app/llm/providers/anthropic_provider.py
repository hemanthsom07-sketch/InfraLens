"""AnthropicProvider (Phase 5, Stage 5G).

A real LLMProvider backed by Anthropic's Messages API, called directly
over HTTPS via the Python standard library (urllib.request + json) — no
SDK or third-party HTTP client dependency.

Configuration is read from environment variables only, never hardcoded
and never logged:
    ANTHROPIC_API_KEY       required for is_available() to be True.
    INFRALENS_LLM_MODEL     optional, overrides the default model.
    INFRALENS_LLM_BASE_URL  optional, overrides https://api.anthropic.com.

Like every other provider, this module accepts only the provider-
agnostic LLMRequest and returns only LLMResponse — it never imports or
references GraphEngine, EvidencePackage, networkx, or anything from
app.api/app.explanation.

ERROR MAPPING: every failure — missing configuration, network error,
non-2xx HTTP status, malformed JSON, or a JSON body missing the fields a
response is expected to have — becomes LLMProviderError, a subclass of
LLMUnavailableError. app.explanation.engine.ExplanationEngine's existing
`except LLMUnavailableError` therefore keeps catching a real provider's
failures unchanged, falling back to the deterministic template path
exactly like it already does for an unconfigured NullProvider.

TESTABILITY: the actual HTTP call is behind a `transport` callable,
injectable at construction (default: _default_transport, the real
urllib-based implementation). Every test in tests/test_anthropic_provider.py
injects a fake transport, so _default_transport is never invoked during a
test run and no test makes a real network call.
"""

import json
import urllib.request
from collections.abc import Callable
from os import environ
from typing import Any

from app.llm.exceptions import LLMProviderError, LLMUnavailableError
from app.llm.models import LLMRequest, LLMResponse
from app.llm.provider import LLMProvider

_DEFAULT_BASE_URL = "https://api.anthropic.com"
# Confirm/override the current model identifier via INFRALENS_LLM_MODEL —
# Anthropic's model lineup changes over time and this default should be
# revisited rather than trusted indefinitely.
_DEFAULT_MODEL = "claude-3-5-haiku-latest"
_ANTHROPIC_API_VERSION = "2023-06-01"
_MESSAGES_PATH = "/v1/messages"

Transport = Callable[[str, str, dict[str, Any], float], dict[str, Any]]


def _default_transport(base_url: str, api_key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """The real HTTP call: POST `payload` to Anthropic's Messages API and
    return the parsed JSON response body.

    stdlib-only (urllib.request + json) — deliberately never invoked by
    any test; see module docstring.
    """
    url = base_url.rstrip("/") + _MESSAGES_PATH
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (internal, fixed https URL)
        return json.loads(response.read().decode("utf-8"))


def _extract_text(raw: dict[str, Any], provider_name: str) -> str:
    """Pull the generated text out of a Messages API response body.

    Raises LLMProviderError if `raw` doesn't have the shape a successful
    response is expected to have. This is what turns a malformed or
    unexpected response into the same safe-fallback path as any other
    provider failure, instead of letting an uncaught
    KeyError/IndexError/TypeError escape this module.
    """
    try:
        text = raw["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMProviderError(provider_name, "unexpected response shape") from exc

    if not isinstance(text, str):
        raise LLMProviderError(provider_name, "unexpected response shape")

    return text


class AnthropicProvider(LLMProvider):
    """A real LLMProvider backed by Anthropic's Messages API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else environ.get("ANTHROPIC_API_KEY")
        self._model = model or environ.get("INFRALENS_LLM_MODEL", _DEFAULT_MODEL)
        self._base_url = base_url or environ.get("INFRALENS_LLM_BASE_URL", _DEFAULT_BASE_URL)
        self._timeout = timeout
        self._transport: Transport = transport or _default_transport

    @property
    def name(self) -> str:
        return "anthropic"

    def is_available(self) -> bool:
        """True only if an API key is configured (non-None, non-empty).

        Deliberately a local, synchronous check that never makes a
        network call — this is called on every
        app.explanation.engine.ExplanationEngine construction via
        get_provider(), so it must stay cheap and side-effect-free.
        """
        return bool(self._api_key)

    def generate(self, request: LLMRequest) -> LLMResponse:
        """LLMRequest -> Anthropic Messages API call -> LLMResponse.

        Raises LLMUnavailableError if no API key is configured. Raises
        LLMProviderError (a subclass of LLMUnavailableError) if a
        configured call still fails for any reason: network error,
        non-2xx HTTP status, invalid JSON, or an unexpected response
        shape. Callers written against LLMUnavailableError (Stage 5D's
        ExplanationEngine) don't need to know or care which one
        occurred — both fall back to the deterministic template path.
        """
        if not self.is_available():
            raise LLMUnavailableError(self.name)

        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system is not None:
            payload["system"] = request.system

        try:
            raw = self._transport(self._base_url, self._api_key, payload, self._timeout)
        except LLMProviderError:
            raise  # a fake transport in a test may raise this directly — don't double-wrap it
        except Exception as exc:
            # Any transport-level failure — network error, non-2xx status,
            # invalid JSON, timeout, ... — becomes LLMProviderError.
            # Deliberately never includes the original exception's message
            # (which could echo a response body) — only its type name.
            raise LLMProviderError(self.name, type(exc).__name__) from exc

        return LLMResponse(text=_extract_text(raw, self.name), provider_name=self.name)
