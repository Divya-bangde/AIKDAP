"""Sprint 9A: LLM gateway unit tests.

Every test here is offline. `litellm.acompletion` is replaced with a
mock, so no test reaches Gemini, spends money, or depends on network
availability. Live connectivity is proven separately by the manual
Phase 8 check, not by this suite.

The API-key tests matter most: they assert that a configured secret is
handed to the provider but never reaches a log line or an exception
message, including when the provider itself echoes it back in an error.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.llm import gateway as gateway_module
from app.core.llm.gateway import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMGateway,
    LLMMessage,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
    get_llm_gateway,
    provider_of,
    scrub_secrets,
)
from app.core.llm.health import check_llm_health

# A fake key shaped like a real Google credential, so the redaction
# patterns are exercised the way they would be in production.
FAKE_KEY = "AIzaSyTESTKEY_MUST_NEVER_BE_LOGGED_0123456789"

GEMINI_MODEL = "gemini/gemini-1.5-pro"


def fake_completion(
    content: str = "AIKDAP is an AI work operating system.",
    *,
    model: str = "gemini/gemini-1.5-pro",
    finish_reason: str = "stop",
    usage: dict[str, int] | None = None,
) -> SimpleNamespace:
    """Build a stand-in for a LiteLLM `ModelResponse`."""
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            model_dump=lambda: usage
            or {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        ),
    )


@pytest.fixture
def gemini_key(monkeypatch) -> str:
    """Configure a fake Gemini credential for the duration of a test."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_KEY))
    return FAKE_KEY


@pytest.fixture
def mock_completion(monkeypatch) -> AsyncMock:
    """Replace LiteLLM's async completion with a mock."""
    mock = AsyncMock(return_value=fake_completion())
    monkeypatch.setattr(gateway_module, "acompletion", mock)
    return mock


# ---------------------------------------------------------------------------
# TEST 1 — the gateway initializes correctly
# ---------------------------------------------------------------------------


def test_gateway_initializes_from_settings():
    """Defaults must come from configuration, not from hardcoded values."""
    client = LLMGateway()
    assert client.default_model == settings.default_llm
    assert client._temperature == settings.llm_temperature
    assert client._max_tokens == settings.llm_max_tokens
    assert client._timeout == settings.llm_timeout


def test_gateway_accepts_explicit_overrides():
    """Per-instance overrides must win over configuration."""
    client = LLMGateway(
        default_model="gemini/gemini-1.5-flash",
        temperature=0.9,
        max_tokens=64,
        timeout=5.0,
    )
    assert client.default_model == "gemini/gemini-1.5-flash"
    assert client._temperature == 0.9
    assert client._max_tokens == 64
    assert client._timeout == 5.0


def test_empty_fallback_setting_disables_fallback(monkeypatch):
    """`FALLBACK_LLM=` in .env must mean "no fallback", not model ""."""
    monkeypatch.setattr(settings, "fallback_llm", "")
    assert LLMGateway().fallback_model is None


def test_get_llm_gateway_returns_shared_instance():
    """The module-level accessor must not build a client per call."""
    assert get_llm_gateway() is get_llm_gateway()


def test_provider_is_derived_from_the_model_id():
    """Provider routing comes from LiteLLM's `provider/model` notation."""
    assert provider_of("gemini/gemini-1.5-pro") == "gemini"
    assert provider_of("ollama/qwen2.5:4b") == "ollama"
    assert provider_of("gpt-4.1-mini") == "default"


# ---------------------------------------------------------------------------
# TEST 2 & 3 — the correct model, system prompt, and user prompt are sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_sends_the_configured_model(gemini_key, mock_completion):
    """The default model must be forwarded to LiteLLM verbatim."""
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.generate(prompt="Explain AIKDAP in three concise sentences.")

    assert mock_completion.await_count == 1
    assert mock_completion.await_args.kwargs["model"] == GEMINI_MODEL


@pytest.mark.asyncio
async def test_explicit_model_overrides_the_default(gemini_key, mock_completion):
    """A per-call model must take precedence over the configured one."""
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.generate(prompt="hello", model="gemini/gemini-1.5-flash")

    assert mock_completion.await_args.kwargs["model"] == "gemini/gemini-1.5-flash"


@pytest.mark.asyncio
async def test_system_and_user_prompts_are_passed_in_order(gemini_key, mock_completion):
    """System prompt first, user prompt second, both intact."""
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.generate(
        prompt="Explain AIKDAP in three concise sentences.",
        system_prompt="You are a precise technical writer.",
    )

    assert mock_completion.await_args.kwargs["messages"] == [
        {"role": "system", "content": "You are a precise technical writer."},
        {"role": "user", "content": "Explain AIKDAP in three concise sentences."},
    ]


@pytest.mark.asyncio
async def test_generation_parameters_are_forwarded(gemini_key, mock_completion):
    """Temperature, max tokens, and timeout must reach the provider."""
    client = LLMGateway(default_model=GEMINI_MODEL, temperature=0.1, max_tokens=256, timeout=12.0)
    await client.generate(prompt="hello")

    kwargs = mock_completion.await_args.kwargs
    assert kwargs["temperature"] == 0.1
    assert kwargs["max_tokens"] == 256
    assert kwargs["timeout"] == 12.0


@pytest.mark.asyncio
async def test_multi_turn_conversation_is_passed_through(gemini_key, mock_completion):
    """`complete()` must forward a full conversation unchanged."""
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.complete(
        messages=[
            LLMMessage(role="system", content="Be terse."),
            LLMMessage(role="user", content="First question."),
            LLMMessage(role="assistant", content="First answer."),
            LLMMessage(role="user", content="Second question."),
        ]
    )

    roles = [m["role"] for m in mock_completion.await_args.kwargs["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


@pytest.mark.asyncio
async def test_structured_response_format_is_forwarded(gemini_key, mock_completion):
    """Structured-output requests must reach the provider when asked for."""
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.generate(prompt="hello", response_format={"type": "json_object"})

    assert mock_completion.await_args.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_response_format_is_omitted_when_not_requested(gemini_key, mock_completion):
    """No `response_format` key unless the caller asked for one."""
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.generate(prompt="hello")

    assert "response_format" not in mock_completion.await_args.kwargs


@pytest.mark.asyncio
async def test_ollama_json_mode_is_routed_through_ollama_chat(mock_completion):
    """`ollama/...` + a response_format must call `ollama_chat/...`.

    Sprint 16 Phase 8.3: reproduced against a real local model that
    `ollama/qwen3.5:4b` in JSON mode returns success with blank content
    (LiteLLM's plain `ollama` transform reads only Ollama's `response`
    field; a hybrid-reasoning model puts the whole answer in `thinking`
    instead). `ollama_chat/...` reproduced correctly against the same
    model and prompt, so the gateway rewrites to it whenever a
    response_format is requested.
    """
    client = LLMGateway(default_model="ollama/qwen3.5:4b")
    await client.generate(prompt="hello", response_format={"type": "json_object"})

    assert mock_completion.await_args.kwargs["model"] == "ollama_chat/qwen3.5:4b"


@pytest.mark.asyncio
async def test_ollama_json_mode_defaults_thinking_off(mock_completion):
    """A real 30-page document under the full target schema reproduced a
    second, deeper failure past the endpoint fix above: `ollama_chat`
    alone still returned blank `content` because `<think>` consumed the
    whole token budget. Forcing `think: false` on the same real document
    and schema fixed it (15s, real content) -- so the JSON-mode rewrite
    also defaults thinking off, unless the caller already has an opinion.
    """
    client = LLMGateway(default_model="ollama/qwen3.5:4b")
    await client.generate(prompt="hello", response_format={"type": "json_object"})

    assert mock_completion.await_args.kwargs["think"] is False


@pytest.mark.asyncio
async def test_ollama_json_mode_respects_an_explicit_think_true(mock_completion):
    """The default must not override a caller that actually wants reasoning on."""
    client = LLMGateway(default_model="ollama/qwen3.5:4b")
    await client.generate(prompt="hello", response_format={"type": "json_object"}, think=True)

    assert mock_completion.await_args.kwargs["think"] is True


@pytest.mark.asyncio
async def test_ollama_without_response_format_is_left_alone(mock_completion):
    """No response_format, no rewrite -- the bug only reproduces in JSON mode."""
    client = LLMGateway(default_model="ollama/qwen3.5:4b")
    await client.generate(prompt="hello")

    assert mock_completion.await_args.kwargs["model"] == "ollama/qwen3.5:4b"


@pytest.mark.asyncio
async def test_ollama_chat_requested_directly_is_not_double_rewritten(mock_completion):
    """A caller that already wrote `ollama_chat/...` is left untouched."""
    client = LLMGateway(default_model="ollama_chat/qwen3.5:4b")
    await client.generate(prompt="hello", response_format={"type": "json_object"})

    assert mock_completion.await_args.kwargs["model"] == "ollama_chat/qwen3.5:4b"


@pytest.mark.asyncio
async def test_think_is_forwarded_to_an_ollama_model(mock_completion):
    """Ollama's hybrid-reasoning toggle must still reach Ollama itself."""
    client = LLMGateway(default_model="ollama_chat/qwen3.5:4b")
    await client.generate(prompt="hello", think=False)

    assert mock_completion.await_args.kwargs["think"] is False


@pytest.mark.asyncio
async def test_num_ctx_is_forwarded_only_to_ollama(gemini_key, mock_completion):
    client = LLMGateway(default_model="ollama_chat/qwen3.5:4b")
    await client.generate(prompt="hello", num_ctx=16_384)
    assert mock_completion.await_args.kwargs["num_ctx"] == 16_384

    mock_completion.reset_mock()
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.generate(prompt="hello", num_ctx=16_384)
    assert "num_ctx" not in mock_completion.await_args.kwargs


@pytest.mark.asyncio
async def test_ollama_never_runs_on_its_default_context_window(gemini_key, mock_completion):
    """Unset, Ollama's 2048-token default silently truncates long prompts."""
    client = LLMGateway(default_model="ollama_chat/qwen3.5:4b")
    await client.generate(prompt="hello")
    assert mock_completion.await_args.kwargs["num_ctx"] == settings.qwen_num_ctx


@pytest.mark.asyncio
async def test_think_is_not_forwarded_to_a_non_ollama_model(gemini_key, mock_completion):
    """`think` is Ollama-specific and must not reach an unrelated provider.

    Sprint 12.6 Phase 3: sent unconditionally, it was rejected by Groq
    as an unsupported request property on every fallback attempt.
    """
    client = LLMGateway(default_model=GEMINI_MODEL)
    await client.generate(prompt="hello", think=False)

    assert "think" not in mock_completion.await_args.kwargs


@pytest.mark.asyncio
async def test_think_is_not_forwarded_to_a_non_ollama_fallback(monkeypatch):
    """A `think` request for the primary must not leak into its fallback.

    Reproduces the Sprint 12.6 Phase 2 finding directly: Qwen (Ollama)
    fails, the chain falls back to Groq, and Groq must never see the
    Ollama-only `think` key that caused its `LLMInvalidRequestError`.
    """
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_TEST_KEY_MUST_NEVER_BE_LOGGED"))
    mock = AsyncMock(
        side_effect=[
            RuntimeError("connection refused"),
            fake_completion(content="from fallback", model="groq/openai/gpt-oss-120b"),
        ]
    )
    monkeypatch.setattr(gateway_module, "acompletion", mock)

    client = LLMGateway(
        default_model="ollama_chat/qwen3.5:4b",
        fallback_model="groq/openai/gpt-oss-120b",
        max_retries=0,
    )
    response = await client.generate(prompt="hello", think=False)

    assert response.fallback_used is True
    assert "think" in mock.await_args_list[0].kwargs  # the Ollama attempt
    assert "think" not in mock.await_args_list[1].kwargs  # the Groq fallback


# ---------------------------------------------------------------------------
# TEST 4 — a successful response is returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_response_is_returned(gemini_key, mock_completion):
    """A completion becomes a clean application-level response."""
    client = LLMGateway(default_model=GEMINI_MODEL)
    response = await client.generate(prompt="Explain AIKDAP in three concise sentences.")

    assert isinstance(response, LLMResponse)
    assert response.content == "AIKDAP is an AI work operating system."
    assert response.model == GEMINI_MODEL
    assert response.provider == "gemini"
    assert response.finish_reason == "stop"
    assert response.prompt_tokens == 11
    assert response.completion_tokens == 7
    assert response.total_tokens == 18
    assert response.latency_ms >= 0
    # No LiteLLM object leaks into the application layer.
    assert not hasattr(response, "choices")


@pytest.mark.asyncio
async def test_empty_choices_raise_rather_than_returning_blank(gemini_key, monkeypatch):
    """A provider returning nothing must fail, not yield an empty answer."""
    monkeypatch.setattr(
        gateway_module,
        "acompletion",
        AsyncMock(return_value=SimpleNamespace(model=GEMINI_MODEL, choices=[], usage=None)),
    )
    with pytest.raises(LLMProviderError, match="no completion choices"):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")


@pytest.mark.asyncio
async def test_null_content_raises_rather_than_returning_none(gemini_key, monkeypatch):
    """A choice with no content must fail loudly."""
    broken = SimpleNamespace(
        model=GEMINI_MODEL,
        choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="stop")],
        usage=None,
    )
    monkeypatch.setattr(gateway_module, "acompletion", AsyncMock(return_value=broken))
    # Sprint 16 Phase 8.3: `None` and `""` are now the same failure (a
    # real local model returned "" with finish_reason=stop and a nonzero
    # token count, which the old `is None` check let through as success).
    with pytest.raises(LLMProviderError, match="empty content"):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")


@pytest.mark.asyncio
async def test_blank_content_raises_rather_than_returning_success(gemini_key, monkeypatch):
    """A choice with `content=""` must fail loudly too, not just `None`.

    Sprint 16 Phase 8.3: reproduced against a real local Ollama model
    (qwen3.5, a hybrid-reasoning model) in JSON mode -- it returned
    `finish_reason="stop"` and a nonzero token count with blank content,
    which read as a normal successful response before this guard existed.
    """
    broken = SimpleNamespace(
        model=GEMINI_MODEL,
        choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")],
        usage=None,
    )
    monkeypatch.setattr(gateway_module, "acompletion", AsyncMock(return_value=broken))
    with pytest.raises(LLMProviderError, match="empty content"):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")


# ---------------------------------------------------------------------------
# TEST 5 & 6 — provider errors and timeouts become application exceptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_error_becomes_application_exception(gemini_key, monkeypatch):
    """A raw provider failure must not escape as a LiteLLM exception."""
    monkeypatch.setattr(
        gateway_module,
        "acompletion",
        AsyncMock(side_effect=RuntimeError("upstream 503 service unavailable")),
    )
    with pytest.raises(LLMProviderError, match="503"):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")


@pytest.mark.asyncio
async def test_timeout_becomes_llm_timeout_error(gemini_key, monkeypatch):
    """`TimeoutError` must map onto the gateway's timeout type."""
    monkeypatch.setattr(
        gateway_module, "acompletion", AsyncMock(side_effect=TimeoutError("deadline exceeded"))
    )
    with pytest.raises(LLMTimeoutError):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")


@pytest.mark.asyncio
async def test_timeout_detected_from_message_text(gemini_key, monkeypatch):
    """A provider that only signals a timeout in text is still classified."""
    monkeypatch.setattr(
        gateway_module,
        "acompletion",
        AsyncMock(side_effect=RuntimeError("Request timed out after 60s")),
    )
    with pytest.raises(LLMTimeoutError):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")


@pytest.mark.asyncio
async def test_rejected_credentials_become_authentication_error(gemini_key, monkeypatch):
    """A credential rejection is distinct from a generic provider error."""
    monkeypatch.setattr(
        gateway_module,
        "acompletion",
        AsyncMock(side_effect=RuntimeError("401 Unauthorized: invalid api key")),
    )
    with pytest.raises(LLMAuthenticationError):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")


@pytest.mark.asyncio
async def test_missing_api_key_fails_before_any_network_call(monkeypatch, mock_completion):
    """No key configured must fail fast, without calling the provider."""
    monkeypatch.setattr(settings, "gemini_api_key", None)

    with pytest.raises(LLMConfigurationError, match="GEMINI_API_KEY"):
        await LLMGateway(default_model=GEMINI_MODEL).generate(prompt="hello")
    mock_completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_message_list_is_rejected():
    """An empty conversation is a caller bug, not a provider call."""
    with pytest.raises(LLMConfigurationError, match="At least one message"):
        await LLMGateway(default_model=GEMINI_MODEL).complete(messages=[])


@pytest.mark.asyncio
async def test_fallback_model_is_tried_after_retries_are_exhausted(
    gemini_key, monkeypatch
):
    """A retryable failure falls back — but only after the retries.

    Updated in Sprint 9G, which changed this contract deliberately.
    Before, a 503 fell straight through to the fallback on the first
    failure; the sprint requires transient failures to be retried
    against the primary first (its TEST 2), because a provider that is
    briefly overloaded usually recovers within seconds and switching
    models is the more disruptive answer.

    So the assertion moved rather than relaxed: it now pins the *whole*
    sequence — three attempts at the primary, then the fallback — which
    is a stronger claim than the single hand-off it replaces.
    """
    mock = AsyncMock(
        side_effect=[
            RuntimeError("upstream 503"),
            RuntimeError("upstream 503"),
            RuntimeError("upstream 503"),
            fake_completion(content="from fallback"),
        ]
    )
    monkeypatch.setattr(gateway_module, "acompletion", mock)

    client = LLMGateway(default_model=GEMINI_MODEL, fallback_model="gemini/gemini-1.5-flash")
    response = await client.generate(prompt="hello")

    assert response.content == "from fallback"
    assert [call.kwargs["model"] for call in mock.await_args_list] == [
        GEMINI_MODEL,
        GEMINI_MODEL,
        GEMINI_MODEL,
        "gemini/gemini-1.5-flash",
    ]
    assert response.fallback_used is True


@pytest.mark.asyncio
async def test_fallback_is_not_tried_for_authentication_failures(gemini_key, monkeypatch):
    """A rejected credential would fail identically on the fallback."""
    mock = AsyncMock(side_effect=RuntimeError("401 Unauthorized: invalid api key"))
    monkeypatch.setattr(gateway_module, "acompletion", mock)

    client = LLMGateway(default_model=GEMINI_MODEL, fallback_model="gemini/gemini-1.5-flash")
    with pytest.raises(LLMAuthenticationError):
        await client.generate(prompt="hello")
    assert mock.await_count == 1


# ---------------------------------------------------------------------------
# TEST 7 — the API key never leaks
# ---------------------------------------------------------------------------


def test_scrub_secrets_redacts_credential_shapes():
    """The redaction helper must catch the common credential formats."""
    assert FAKE_KEY not in scrub_secrets(f"api_key={FAKE_KEY}")
    assert FAKE_KEY not in scrub_secrets(f"Authorization: Bearer {FAKE_KEY}")
    assert FAKE_KEY not in scrub_secrets(f"key leaked bare: {FAKE_KEY}")
    # Non-secret text must survive untouched, or errors become useless.
    assert scrub_secrets("model gemini-1.5-pro is overloaded") == (
        "model gemini-1.5-pro is overloaded"
    )


@pytest.mark.asyncio
async def test_embedding_sends_the_provider_credential(monkeypatch):
    """Regression: `embed()` resolved no credentials at all.

    Embeddings only ever ran against Ollama, which needs no key, so the
    omission was invisible until a hosted embedding model was
    configured — LiteLLM then sent an unauthenticated request and the
    provider answered AUTH_MISSING_API_KEY. Caught against the real
    Jina API, not in review.
    """
    monkeypatch.setattr(settings, "jina_api_key", SecretStr(FAKE_KEY))
    mock = AsyncMock(return_value=SimpleNamespace(data=[{"embedding": [0.1] * 8}]))
    monkeypatch.setattr(gateway_module, "aembedding", mock)

    await LLMGateway().embed(texts=["one"], model="jina_ai/jina-embeddings-v3")

    assert mock.await_args.kwargs["api_key"] == FAKE_KEY


@pytest.mark.asyncio
async def test_ollama_embedding_sends_a_base_url_and_no_credential(monkeypatch):
    """The local path must keep working exactly as before: Ollama takes
    an `api_base` and no key, and registering cloud credentials must not
    start sending one to it."""
    mock = AsyncMock(return_value=SimpleNamespace(data=[{"embedding": [0.1] * 8}]))
    monkeypatch.setattr(gateway_module, "aembedding", mock)

    await LLMGateway().embed(texts=["one"], model="ollama/bge-m3")

    assert "api_key" not in mock.await_args.kwargs
    assert mock.await_args.kwargs["api_base"] == settings.ollama_base_url


@pytest.mark.asyncio
async def test_embedding_is_sent_in_batches_and_reassembled_in_order(monkeypatch):
    """Regression: a whole textbook (2937 chunks) in one request crashed
    Ollama's model runner and failed every chunk of the upload. The
    texts must go out in slices and come back as one ordered list."""

    async def embed_each(**kwargs):
        # Each vector encodes its text's length, so order is checkable.
        return SimpleNamespace(data=[{"embedding": [float(len(text)), 0.0]} for text in kwargs["input"]])

    mock = AsyncMock(side_effect=embed_each)
    monkeypatch.setattr(gateway_module, "aembedding", mock)
    texts = ["x" * (i + 1) for i in range(150)]

    response = await LLMGateway().embed(texts=texts, model="ollama/bge-m3")

    assert [len(call.kwargs["input"]) for call in mock.await_args_list] == [64, 64, 22]
    assert [vector[0] for vector in response.vectors] == [float(i + 1) for i in range(150)]


@pytest.mark.asyncio
async def test_api_key_is_sent_to_provider_but_never_logged_or_raised(
    gemini_key, monkeypatch, caplog
):
    """The end-to-end secrecy guarantee.

    The provider echoes the key back in its error message — the worst
    realistic case. The key must still reach LiteLLM (or nothing would
    work) while appearing in neither the raised exception nor the logs.
    """
    mock = AsyncMock(side_effect=RuntimeError(f"Invalid request: api_key={FAKE_KEY}"))
    monkeypatch.setattr(gateway_module, "acompletion", mock)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(LLMProviderError) as exc_info:
            # `allow_fallback=False` pins this to the one provider whose
            # secrecy is under test. Without it the call would continue
            # down the deployment's configured chain and `await_args`
            # would describe a different provider's request entirely.
            await LLMGateway(default_model=GEMINI_MODEL).generate(
                prompt="hello", allow_fallback=False
            )

    # The key really was handed to the provider.
    assert mock.await_args.kwargs["api_key"] == FAKE_KEY

    # ...but never surfaced anywhere observable.
    assert FAKE_KEY not in str(exc_info.value)
    assert FAKE_KEY not in repr(exc_info.value)
    assert FAKE_KEY not in caplog.text


@pytest.mark.asyncio
async def test_successful_call_never_logs_the_key_or_the_prompt_payload(
    gemini_key, mock_completion, caplog
):
    """A normal call must not put the credential in the log stream."""
    with caplog.at_level(logging.DEBUG):
        await LLMGateway(default_model=GEMINI_MODEL).generate(
            prompt="hello", system_prompt="be terse"
        )

    assert FAKE_KEY not in caplog.text
    assert "llm_request_completed" in caplog.text


def test_settings_repr_masks_the_api_key(gemini_key):
    """Rendering settings must not expose the secret."""
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings.gemini_api_key)
    # The value is still retrievable at the single point of use.
    assert settings.gemini_api_key.get_secret_value() == FAKE_KEY


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_reports_success_metadata(gemini_key, monkeypatch):
    """A reachable model yields safe, useful metadata."""
    monkeypatch.setattr(
        gateway_module, "acompletion", AsyncMock(return_value=fake_completion(content="OK"))
    )

    health = await check_llm_health(gateway=LLMGateway(default_model=GEMINI_MODEL))

    assert health.success is True
    assert health.provider == "gemini"
    assert health.model == GEMINI_MODEL
    assert health.latency_ms >= 0
    assert health.error_type is None
    assert health.sample == "OK"
    assert FAKE_KEY not in str(health.as_dict())


@pytest.mark.asyncio
async def test_health_check_reports_failure_without_raising(gemini_key, monkeypatch):
    """An unreachable model is a result, not an exception."""
    monkeypatch.setattr(
        gateway_module, "acompletion", AsyncMock(side_effect=RuntimeError("upstream 503"))
    )

    health = await check_llm_health(gateway=LLMGateway(default_model=GEMINI_MODEL))

    assert health.success is False
    # Sprint 9G split the old catch-all `LLMProviderError` into a
    # taxonomy the retry policy routes on, so a 503 now reports as the
    # specific failure it is. `LLMServiceUnavailableError` is still a
    # subclass of `LLMProviderError` — only the reported name is more
    # precise, and precision is the point: an operator reading this
    # can tell "the provider is overloaded" from "we do not know".
    assert health.error_type == "LLMServiceUnavailableError"
    assert "503" in health.error_message
    assert health.sample is None


@pytest.mark.asyncio
async def test_health_check_reports_missing_credentials_without_calling_provider(
    monkeypatch, mock_completion
):
    """No key configured must be reported, not attempted."""
    monkeypatch.setattr(settings, "gemini_api_key", None)

    health = await check_llm_health(gateway=LLMGateway(default_model=GEMINI_MODEL))

    assert health.success is False
    assert health.error_type == "LLMConfigurationError"
    assert "GEMINI_API_KEY" in health.error_message
    mock_completion.assert_not_awaited()
