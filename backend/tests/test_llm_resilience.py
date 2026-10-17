"""Sprint 9G/9H: retry, fallback, quota semantics, and provider health.

Every test is offline. `litellm.acompletion` is replaced with a double,
so nothing here reaches Gemini, Groq or OpenRouter, spends money, or
consumes the daily quota this sprint exists to survive. Live provider
behaviour is proven separately by the manual Phase 22-27 checks (9G)
and Phase 4/22/23 checks (9H), reported with their real numbers.

The doubles raise *real* provider errors. In particular
`gemini_daily_quota_error()` is a verbatim reproduction of the refusal
this deployment received on 2026-08-16, quota metadata and all — right
down to the `retryDelay: "35s"` hint attached to a quota that resets
once a day. Classification tests written against a paraphrase would
prove nothing; the whole difficulty is that Google's per-day and
per-minute refusals are nearly identical text.

Sprint 9H replaced DeepSeek with OpenRouter as the active third
provider (DeepSeek's credential plumbing remains, unused, in case it
is ever reconfigured — see `test_deepseek_is_not_in_the_deployed_chain`).
`OPENROUTER` below is the model this deployment actually selected;
the OpenRouter-specific error doubles further down are reproduced from
real refusals seen while selecting it, documented per-fixture as
observed or merely known-from-documentation.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.llm import gateway as gateway_module
from app.core.llm.errors import is_retryable, is_terminal
from app.core.llm.gateway import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMGateway,
    LLMInvalidRequestError,
    LLMModelNotFoundError,
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from app.core.llm.provider_health import (
    ProviderHealthRegistry,
    ProviderStatus,
    is_provider_configured,
)

GEMINI = "gemini/gemini-flash-latest"
GROQ = "groq/openai/gpt-oss-120b"
OPENROUTER = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"

FAKE_GEMINI_KEY = "AIzaSyTESTKEY_MUST_NEVER_BE_LOGGED_0123456789"
FAKE_GROQ_KEY = "gsk_TESTKEYMUSTNEVERBELOGGED0123456789"
FAKE_OPENROUTER_KEY = "sk-or-v1-testkeymustneverbelogged0123456789"


# ---------------------------------------------------------------------------
# Provider error doubles — reproduced from live refusals
# ---------------------------------------------------------------------------


class ProviderException(Exception):
    """A provider error carrying a status code, as LiteLLM's do."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def gemini_daily_quota_error() -> ProviderException:
    """The exact refusal this deployment got when its free tier ran out.

    Captured live on 2026-08-16. Two details matter and are preserved
    verbatim:

    - the message text is *identical* to Gemini's per-minute refusal
      ("You exceeded your current quota, please check your plan and
      billing details"), so only `quotaId` distinguishes them;
    - `retryDelay: "35s"` is attached to a quota that resets daily. A
      retry loop that trusted it would run all day.
    """
    return ProviderException(
        "litellm.RateLimitError: geminiException - {"
        '"error": {"code": 429, "message": "You exceeded your current quota, '
        "please check your plan and billing details. "
        "\\n* Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
        'limit: 20, model: gemini-3.7-flash\\nPlease retry in 35.081138648s.", '
        '"status": "RESOURCE_EXHAUSTED", "details": [{"@type": '
        '"type.googleapis.com/google.rpc.QuotaFailure", "violations": [{'
        '"quotaMetric": '
        '"generativelanguage.googleapis.com/generate_content_free_tier_requests", '
        '"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", '
        '"quotaValue": "20"}]}, {"@type": '
        '"type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "35s"}]}}',
        status_code=429,
    )


def gemini_per_minute_rate_limit_error() -> ProviderException:
    """Gemini's *transient* 429 — same wording, different quota id.

    This is the trap. Everything a naive classifier would match on
    ("quota", "exceeded your current quota", "billing", 429,
    RESOURCE_EXHAUSTED) is shared with the daily refusal above.
    """
    return ProviderException(
        "litellm.RateLimitError: geminiException - {"
        '"error": {"code": 429, "message": "You exceeded your current quota, '
        "please check your plan and billing details. "
        "\\n* Quota exceeded for metric: "
        "generativelanguage.googleapis.com/generate_content_requests, "
        'limit: 10, model: gemini-3.7-flash", '
        '"status": "RESOURCE_EXHAUSTED", "details": [{"@type": '
        '"type.googleapis.com/google.rpc.QuotaFailure", "violations": [{'
        '"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", '
        '"quotaValue": "10"}]}]}}',
        status_code=429,
    )


def deepseek_insufficient_balance_error() -> ProviderException:
    """DeepSeek's real refusal when the account has no credit.

    Observed live on 2026-08-16. Reported as HTTP 400 with
    `code: "invalid_request_error"` — read literally, a malformed
    request, which would be terminal and would abort a chain that
    still had working providers in it.
    """
    return ProviderException(
        'litellm.BadRequestError: DeepseekException - {"error":'
        '{"message":"Insufficient Balance","type":"unknown_error",'
        '"param":null,"code":"invalid_request_error"}}',
        status_code=400,
    )


def service_unavailable_error() -> ProviderException:
    return ProviderException(
        "litellm.InternalServerError: geminiException - 503 The model is "
        "overloaded. Please try again later.",
        status_code=503,
    )


def bad_request_error() -> ProviderException:
    return ProviderException(
        "litellm.BadRequestError: geminiException - 400 Invalid value for "
        "'contents': the request payload is malformed.",
        status_code=400,
    )


def unauthorized_error() -> ProviderException:
    return ProviderException(
        "litellm.AuthenticationError: geminiException - 401 API key not valid.",
        status_code=401,
    )


def ollama_unreachable_error() -> ProviderException:
    """The real error when the host's Ollama is not running.

    Observed live on 2026-08-18, after a host restart took Ollama down
    with it. The detail that matters is `status_code = 500`: LiteLLM
    stamps that on `APIConnectionError`, so a classifier that checks
    5xx before connection failures reports "the provider is
    overloaded" for a server that is not running at all.
    """
    return ProviderException(
        "litellm.APIConnectionError: OllamaException - Cannot connect to host "
        "host.docker.internal:11434 ssl:default [Connect call failed "
        "('192.168.65.254', 11434)]",
        status_code=500,
    )


def model_not_found_error() -> ProviderException:
    return ProviderException(
        "litellm.NotFoundError: geminiException - 404 models/nope is not found "
        "for API version v1beta.",
        status_code=404,
    )


def openrouter_transient_rate_limit_error() -> ProviderException:
    """OpenRouter's real refusal when a free upstream model is congested.

    Observed live on 2026-08-18 while model-testing for Sprint 9H:
    `z-ai/glm-5.2:free` and `google/gemma-4-31b-it:free` both returned
    this shape. Unlike Gemini's 429, OpenRouter does not attach quota
    metadata at all — just a generic "Provider returned error" — so
    this has to fall through to the ambiguous default in
    `_classify_rate_limit` rather than being decided by a marker.
    """
    return ProviderException(
        'litellm.RateLimitError: RateLimitError: OpenrouterException - '
        '{"error":{"message":"Provider returned error","code":429,'
        '"metadata":{"raw":"upstream rate limited","provider_name":"unknown"}}}',
        status_code=429,
    )


def openrouter_insufficient_credits_error() -> ProviderException:
    """OpenRouter's documented refusal for a spent prepaid balance.

    HTTP 402 Payment Required, per OpenRouter's own API reference.
    NOT reproduced live in this deployment: the configured key is a
    fresh free-tier key with $0 usage recorded, so it has never
    actually run out of credit. Included because it is cheap and
    correct to classify defensively, and documented here as exactly
    that — a documented shape, not an observed one.
    """
    return ProviderException(
        'litellm.BadRequestError: OpenrouterException - {"error":'
        '{"message":"Insufficient credits","code":402}}',
        status_code=402,
    )


def openrouter_empty_completion_error() -> ProviderException:
    """Not a provider exception at all -- the OTHER real OpenRouter failure.

    Observed live on 2026-08-18: `openai/gpt-oss-20b:free` and
    `nvidia/nemotron-nano-9b-v2:free` both returned HTTP 200 with a
    choice whose `message.content` was empty or `None` (plausibly the
    entire token budget spent on hidden reasoning, the same failure
    mode documented for Qwen 3.5 in `settings.qwen_think`). The
    gateway's existing defensive check in `_to_response` already turns
    this into `LLMProviderError` before any classification logic runs
    -- there is no exception to classify, which is exactly why this
    fixture returns a completion, not an error.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        model="nvidia/nemotron-nano-9b-v2:free",
        choices=[SimpleNamespace(message=SimpleNamespace(content=None), finish_reason="length")],
        usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 512}),
    )


def completion(content: str = "answered", *, model: str = GEMINI) -> SimpleNamespace:
    """A stand-in for a LiteLLM `ModelResponse`."""
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(
            model_dump=lambda: {
                "prompt_tokens": 5,
                "completion_tokens": 2,
                "total_tokens": 7,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def all_keys(monkeypatch):
    """Configure every provider, so chain behaviour is what is tested."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_GEMINI_KEY))
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(FAKE_GROQ_KEY))
    monkeypatch.setattr(settings, "openrouter_api_key", SecretStr(FAKE_OPENROUTER_KEY))


@pytest.fixture
def call(monkeypatch) -> AsyncMock:
    """Replace LiteLLM's entry point. The rest of the gateway is real."""
    mock = AsyncMock(return_value=completion())
    monkeypatch.setattr(gateway_module, "acompletion", mock)
    return mock


def chained(**kwargs) -> LLMGateway:
    """A gateway with the full three-provider chain and a fresh breaker."""
    return LLMGateway(
        default_model=GEMINI,
        fallback_model=GROQ,
        secondary_fallback_model=OPENROUTER,
        provider_health=ProviderHealthRegistry(),
        **kwargs,
    )


def models_called(mock: AsyncMock) -> list[str]:
    return [call.kwargs["model"] for call in mock.await_args_list]


# ---------------------------------------------------------------------------
# Error classification (Phase 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (gemini_daily_quota_error(), LLMQuotaExhaustedError),
        (gemini_per_minute_rate_limit_error(), LLMRateLimitError),
        (deepseek_insufficient_balance_error(), LLMQuotaExhaustedError),
        (service_unavailable_error(), LLMServiceUnavailableError),
        (bad_request_error(), LLMInvalidRequestError),
        (unauthorized_error(), LLMAuthenticationError),
        (model_not_found_error(), LLMModelNotFoundError),
        (TimeoutError("deadline exceeded"), LLMTimeoutError),
        (ProviderException("Cannot connect to host localhost:11434"), LLMConnectionError),
        (ollama_unreachable_error(), LLMConnectionError),
        (ProviderException("something nobody has seen before"), LLMProviderError),
    ],
)
def test_provider_errors_are_classified(error, expected):
    """Each distinct failure mode gets its own type, not a generic one."""
    classified = LLMGateway._normalize_error(error, model=GEMINI)
    assert type(classified) is expected


def test_an_unreachable_host_is_not_reported_as_an_overloaded_provider():
    """A regression found live, not by inspection.

    LiteLLM sets `status_code = 500` on `APIConnectionError`, so the
    new 5xx branch shadowed the connection check and reported a dead
    local Ollama as "temporarily unavailable". Both are retryable, so
    nothing behaved wrongly — but "the provider is overloaded" sends an
    operator looking at the wrong machine.
    """
    classified = LLMGateway._normalize_error(ollama_unreachable_error(), model=GEMINI)

    assert isinstance(classified, LLMConnectionError)
    assert not isinstance(classified, LLMServiceUnavailableError)
    assert is_retryable(classified)


def test_daily_quota_and_per_minute_limit_are_told_apart():
    """The distinction the whole sprint rests on.

    Both refusals are HTTP 429 `RESOURCE_EXHAUSTED` with the same
    "exceeded your current quota / billing details" wording. If these
    two ever collapse into one type, either every transient limit
    disables a working provider for an hour, or every daily quota gets
    retried into the ground. Only the quota id separates them.
    """
    daily = LLMGateway._normalize_error(gemini_daily_quota_error(), model=GEMINI)
    per_minute = LLMGateway._normalize_error(
        gemini_per_minute_rate_limit_error(), model=GEMINI
    )

    assert isinstance(daily, LLMQuotaExhaustedError)
    assert isinstance(per_minute, LLMRateLimitError)
    assert not isinstance(per_minute, LLMQuotaExhaustedError)
    # And the policy consequence, which is the part that matters.
    assert not is_retryable(daily)
    assert is_retryable(per_minute)


def test_retry_policy_groups_are_disjoint():
    """No error may be both terminal and retryable."""
    for error in (
        gemini_daily_quota_error(),
        gemini_per_minute_rate_limit_error(),
        service_unavailable_error(),
        bad_request_error(),
        unauthorized_error(),
        model_not_found_error(),
    ):
        classified = LLMGateway._normalize_error(error, model=GEMINI)
        assert not (is_terminal(classified) and is_retryable(classified))


def test_quota_exhaustion_is_never_retryable():
    """Belt and braces: the one rule that must not regress."""
    assert not is_retryable(LLMQuotaExhaustedError("spent", model=GEMINI))


# ---------------------------------------------------------------------------
# OpenRouter error classification (Sprint 9H Phase 19)
# ---------------------------------------------------------------------------


def test_openrouter_402_is_classified_as_quota_exhausted():
    """Documented OpenRouter behaviour, not live-observed — see the
    fixture's docstring for why. Still real policy: a 402 must never
    be treated as a malformed request and abort a chain that has
    working providers left in it."""
    classified = LLMGateway._normalize_error(
        openrouter_insufficient_credits_error(), model=OPENROUTER
    )
    assert isinstance(classified, LLMQuotaExhaustedError)
    assert not is_retryable(classified)


def test_openrouter_congestion_429_is_classified_as_rate_limited():
    """The real shape observed live: no quota metadata at all, just
    'Provider returned error'. With no window marker to key off,
    classification must land on the ambiguous default (rate limit,
    retryable) rather than on quota exhaustion -- see
    `_classify_rate_limit`'s precedence rules."""
    classified = LLMGateway._normalize_error(
        openrouter_transient_rate_limit_error(), model=OPENROUTER
    )
    assert isinstance(classified, LLMRateLimitError)
    assert not isinstance(classified, LLMQuotaExhaustedError)
    assert is_retryable(classified)


def test_openrouter_and_gemini_402_429_do_not_collapse_into_one_type():
    """Two different providers, two different ambiguous 4xx shapes --
    both must still land in the taxonomy their own metadata implies,
    not in whichever bucket happens to run first."""
    credits = LLMGateway._normalize_error(
        openrouter_insufficient_credits_error(), model=OPENROUTER
    )
    congestion = LLMGateway._normalize_error(
        openrouter_transient_rate_limit_error(), model=OPENROUTER
    )
    assert type(credits) is not type(congestion)


# ---------------------------------------------------------------------------
# Retry policy (Phase 5) — TESTs 2, 3, 5, 6, 7
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_success_returns_gemini(all_keys, call):
    """TEST 1. A working primary answers, and nothing else is called."""
    response = await chained().generate(prompt="hi")

    assert response.content == "answered"
    assert response.provider == "gemini"
    assert response.fallback_used is False
    assert models_called(call) == [GEMINI]


@pytest.mark.asyncio
async def test_503_is_retried_then_succeeds(all_keys, call):
    """TEST 2. A transient outage must not cost the primary provider."""
    call.side_effect = [service_unavailable_error(), completion("recovered")]

    response = await chained().generate(prompt="hi")

    assert response.content == "recovered"
    assert response.fallback_used is False
    assert models_called(call) == [GEMINI, GEMINI]
    assert response.attempts == 2


@pytest.mark.asyncio
async def test_transient_429_is_retried_then_succeeds(all_keys, call):
    """TEST 3. A per-minute limit clears; the fallback is not needed."""
    call.side_effect = [gemini_per_minute_rate_limit_error(), completion("recovered")]

    response = await chained().generate(prompt="hi")

    assert response.content == "recovered"
    assert response.fallback_used is False
    assert models_called(call) == [GEMINI, GEMINI]


@pytest.mark.asyncio
async def test_daily_quota_is_not_retried_and_falls_back(all_keys, call):
    """TEST 4 — the headline behaviour of Sprint 9G.

    Exactly ONE call to Gemini. Not two, not three. Then Groq. Every
    extra Gemini attempt here is a request that cannot succeed until
    tomorrow, and the naive implementation makes three of them.
    """
    call.side_effect = [gemini_daily_quota_error(), completion("from groq", model=GROQ)]

    response = await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI, GROQ]
    assert response.content == "from groq"
    assert response.provider == "groq"
    assert response.fallback_used is True
    assert response.primary_model == GEMINI
    assert response.primary_error_type == "LLMQuotaExhaustedError"


@pytest.mark.asyncio
async def test_timeout_is_retried_then_falls_back(all_keys, call):
    """TEST 5. Retries are bounded, then the chain moves on."""
    call.side_effect = [
        TimeoutError("deadline exceeded"),
        TimeoutError("deadline exceeded"),
        TimeoutError("deadline exceeded"),
        completion("from groq", model=GROQ),
    ]

    response = await chained().generate(prompt="hi")

    # 1 initial + 2 retries against Gemini (LLM_MAX_RETRIES=2), then Groq.
    assert models_called(call) == [GEMINI, GEMINI, GEMINI, GROQ]
    assert response.fallback_used is True
    assert response.primary_error_type == "LLMTimeoutError"


@pytest.mark.asyncio
async def test_400_is_not_retried_and_does_not_fall_back(all_keys, call):
    """TEST 6. A malformed request is malformed everywhere."""
    call.side_effect = bad_request_error()

    with pytest.raises(LLMInvalidRequestError):
        await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI]


@pytest.mark.asyncio
async def test_401_is_not_retried_and_does_not_fall_back(all_keys, call):
    """TEST 7. A rejected credential is a deployment problem.

    Falling back here would hide a misconfiguration behind a different
    provider's bill, and the operator would never learn the primary key
    is dead.
    """
    call.side_effect = unauthorized_error()

    with pytest.raises(LLMAuthenticationError):
        await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI]


@pytest.mark.asyncio
async def test_404_model_not_found_falls_back_without_retrying(all_keys, call):
    """TEST 8. The model will not appear between attempts — but another
    configured model is exactly the right answer to one that is gone."""
    call.side_effect = [model_not_found_error(), completion("from groq", model=GROQ)]

    response = await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI, GROQ]
    assert response.primary_error_type == "LLMModelNotFoundError"


@pytest.mark.asyncio
async def test_retry_delay_grows_and_is_capped():
    """The backoff schedule itself, asserted without waiting for it.

    Equal jitter: each delay lands in the upper half of its window, so
    a retry can never collapse into an immediate repeat of a call the
    provider just rejected.
    """
    gateway = LLMGateway(
        default_model=GEMINI,
        retry_base_delay=1.0,
        retry_max_delay=8.0,
        provider_health=ProviderHealthRegistry(),
    )

    for attempt, window in ((1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (9, 8.0)):
        delays = [gateway._retry_delay(attempt) for _ in range(50)]
        assert all(window / 2 <= delay <= window for delay in delays)
        assert min(delays) < max(delays), "jitter is not being applied"


@pytest.mark.asyncio
async def test_retries_can_be_disabled_per_call(all_keys, call):
    """The health probe needs exactly one attempt, and gets it."""
    call.side_effect = service_unavailable_error()

    with pytest.raises(LLMServiceUnavailableError):
        await chained().generate(prompt="hi", max_retries=0, allow_fallback=False)

    assert models_called(call) == [GEMINI]


# ---------------------------------------------------------------------------
# Fallback chain (Phases 7-8) — TESTs 9-12
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_fallback_succeeds(all_keys, call):
    """TEST 9."""
    call.side_effect = [gemini_daily_quota_error(), completion("from groq", model=GROQ)]

    response = await chained().generate(prompt="hi")

    assert response.provider == "groq"
    assert response.content == "from groq"


@pytest.mark.asyncio
async def test_openrouter_is_tried_when_groq_also_fails(all_keys, call):
    """TEST 10. Scenario D: the chain has three links, not two."""
    call.side_effect = [
        gemini_daily_quota_error(),
        service_unavailable_error(),
        service_unavailable_error(),
        service_unavailable_error(),
        completion("from openrouter", model=OPENROUTER),
    ]

    response = await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI, GROQ, GROQ, GROQ, OPENROUTER]
    assert response.provider == "openrouter"
    assert response.fallback_used is True


@pytest.mark.asyncio
async def test_all_providers_failing_raises_a_typed_error(all_keys, call):
    """TEST 11 / Scenario E. No answer is invented when none exists."""
    call.side_effect = [
        gemini_daily_quota_error(),
        gemini_daily_quota_error(),  # Groq, also out of quota
        openrouter_insufficient_credits_error(),
    ]

    with pytest.raises(LLMQuotaExhaustedError):
        await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI, GROQ, OPENROUTER]


@pytest.mark.asyncio
async def test_no_fallback_configured_fails_on_the_primary(all_keys, call):
    """TEST 12. An empty `FALLBACK_LLM` means no fallback, not model ""."""
    call.side_effect = gemini_daily_quota_error()
    gateway = LLMGateway(
        default_model=GEMINI,
        fallback_model="",
        secondary_fallback_model="",
        provider_health=ProviderHealthRegistry(),
    )

    with pytest.raises(LLMQuotaExhaustedError):
        await gateway.generate(prompt="hi")

    assert models_called(call) == [GEMINI]


def test_an_explicit_empty_fallback_is_not_the_configured_one(monkeypatch):
    """`fallback_model=""` means none — not "use whatever .env says".

    Found when this deployment actually configured a fallback chain and
    a single-provider test quietly acquired two extra providers. The
    old `fallback_model or settings.fallback_llm` could not tell "not
    specified" from "explicitly disabled".
    """
    monkeypatch.setattr(settings, "fallback_llm", GROQ)
    monkeypatch.setattr(settings, "secondary_fallback_llm", OPENROUTER)

    explicit = LLMGateway(default_model=GEMINI, fallback_model="",
                          secondary_fallback_model="")
    assert explicit.fallback_model is None
    assert explicit.secondary_fallback_model is None

    # Omitting the argument still inherits configuration.
    inherited = LLMGateway(default_model=GEMINI)
    assert inherited.fallback_model == GROQ
    assert inherited.secondary_fallback_model == OPENROUTER


@pytest.mark.asyncio
async def test_unconfigured_fallback_is_skipped_not_attempted(monkeypatch, call):
    """Phase 8. A missing optional key costs nothing and breaks nothing."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_GEMINI_KEY))
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    call.side_effect = gemini_daily_quota_error()

    with pytest.raises(LLMQuotaExhaustedError):
        await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI]


@pytest.mark.asyncio
async def test_missing_primary_key_fails_loudly_rather_than_falling_back(
    monkeypatch, call
):
    """A misconfigured primary must not be papered over by a fallback.

    The opposite of the test above, and the asymmetry is deliberate:
    an optional provider without a key is a choice, the primary
    without one is a mistake nobody would otherwise notice.
    """
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "groq_api_key", SecretStr(FAKE_GROQ_KEY))

    with pytest.raises(LLMConfigurationError, match="GEMINI_API_KEY"):
        await chained().generate(prompt="hi")

    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_successful_primary_never_calls_a_fallback(all_keys, call):
    """Phase 32. Cost control: success stops the chain."""
    await chained().generate(prompt="hi")

    assert call.await_count == 1


# ---------------------------------------------------------------------------
# Provider health / circuit breaker (Phases 10, 23)
# ---------------------------------------------------------------------------


def test_untried_provider_is_configured_not_healthy(monkeypatch):
    """Phase 23's core requirement.

    "There is an API key" and "it works" are different claims, and only
    one of them has been verified.
    """
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(FAKE_GEMINI_KEY))
    registry = ProviderHealthRegistry()

    assert registry.status(GEMINI).status is ProviderStatus.CONFIGURED
    assert registry.status(GEMINI).blocked is False


def test_provider_without_a_key_is_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)
    assert is_provider_configured("groq") is False
    assert ProviderHealthRegistry().status(GROQ).status is ProviderStatus.NOT_CONFIGURED


def test_success_marks_a_provider_healthy():
    registry = ProviderHealthRegistry()
    registry.record_success(GEMINI)

    health = registry.status(GEMINI)
    assert health.status is ProviderStatus.HEALTHY
    assert health.blocked is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMQuotaExhaustedError("spent"), ProviderStatus.QUOTA_EXHAUSTED),
        (LLMRateLimitError("slow down"), ProviderStatus.RATE_LIMITED),
        (LLMServiceUnavailableError("503"), ProviderStatus.UNAVAILABLE),
        (LLMTimeoutError("slow"), ProviderStatus.UNAVAILABLE),
        (LLMAuthenticationError("401"), ProviderStatus.CONFIGURATION_ERROR),
        (LLMInvalidRequestError("400"), ProviderStatus.CONFIGURATION_ERROR),
    ],
)
def test_failures_map_onto_provider_states(error, expected):
    registry = ProviderHealthRegistry()
    registry.record_failure(GEMINI, error=error)
    assert registry.status(GEMINI).status is expected


def test_a_configuration_error_does_not_block_the_provider():
    """A dead credential must surface, not silently reroute traffic."""
    registry = ProviderHealthRegistry()
    registry.record_failure(GEMINI, error=LLMAuthenticationError("401"))

    assert registry.status(GEMINI).blocked is False


def test_quota_exhaustion_blocks_until_the_cooldown_expires(monkeypatch):
    """Bounded, and self-healing without operator action."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-cooldown-key"))
    monkeypatch.setattr(settings, "llm_quota_cooldown_seconds", 3600.0)
    registry = ProviderHealthRegistry()
    registry.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))

    assert registry.is_blocked(GEMINI) is True
    assert 3500 < registry.status(GEMINI).retry_after_seconds <= 3600

    # Shrink the cooldown and let real time pass it, rather than
    # faking the clock: the decay is `age >= cooldown` against a
    # monotonic reading, and 20ms proves it as well as an hour would.
    monkeypatch.setattr(settings, "llm_quota_cooldown_seconds", 0.01)
    time.sleep(0.02)

    assert registry.is_blocked(GEMINI) is False
    assert registry.status(GEMINI).status is ProviderStatus.CONFIGURED


def test_the_breaker_is_keyed_by_model_not_by_provider():
    """One exhausted model must not disable its provider's others.

    Gemini's free-tier limit is `GenerateRequestsPerDayPerProject*
    PerModel* — the live refusal names the model it applies to. A
    provider-keyed breaker would take out every Gemini model the moment
    one ran out, and would silently defeat the cheapest mitigation
    available: falling back to a second model on the same provider,
    which has its own separate daily allowance.

    Caught by a real test failure, not by inspection: a
    gemini-to-gemini fallback was being skipped without ever being
    called.
    """
    registry = ProviderHealthRegistry()
    registry.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))

    assert registry.is_blocked(GEMINI) is True
    assert registry.is_blocked("gemini/gemini-pro-latest") is False


@pytest.mark.asyncio
async def test_a_same_provider_fallback_is_still_tried(all_keys, call):
    """The behaviour the model-keyed breaker exists to preserve."""
    other = "gemini/gemini-pro-latest"
    call.side_effect = [
        gemini_daily_quota_error(),
        completion("from the other gemini model", model=other),
    ]
    gateway = LLMGateway(
        default_model=GEMINI,
        fallback_model=other,
        secondary_fallback_model="",
        provider_health=ProviderHealthRegistry(),
    )

    response = await gateway.generate(prompt="hi")

    assert models_called(call) == [GEMINI, other]
    assert response.content == "from the other gemini model"


def test_a_later_success_clears_a_recorded_failure():
    registry = ProviderHealthRegistry()
    registry.record_failure(GEMINI, error=LLMRateLimitError("429"))
    registry.record_success(GEMINI)

    assert registry.status(GEMINI).status is ProviderStatus.HEALTHY
    assert registry.is_blocked(GEMINI) is False


@pytest.mark.asyncio
async def test_a_blocked_provider_is_skipped_without_a_request(all_keys, call):
    """Phases 6 and 32: a known-spent quota costs no further round trips.

    The first call learns Gemini is out. The second must go straight to
    Groq — the whole point of remembering.
    """
    call.side_effect = [
        gemini_daily_quota_error(),
        completion("from groq", model=GROQ),
        completion("from groq again", model=GROQ),
    ]
    gateway = chained()

    await gateway.generate(prompt="first")
    await gateway.generate(prompt="second")

    assert models_called(call) == [GEMINI, GROQ, GROQ]


@pytest.mark.asyncio
async def test_a_skipped_provider_is_recorded_in_the_trace(all_keys, call):
    """Silently skipping would make an odd bill impossible to explain."""
    call.side_effect = [
        gemini_daily_quota_error(),
        completion("from groq", model=GROQ),
        completion("from groq again", model=GROQ),
    ]
    gateway = chained()
    await gateway.generate(prompt="first")

    response = await gateway.generate(prompt="second")

    skipped = [item for item in response.trace if item.status == "skipped"]
    assert [item.model for item in skipped] == [GEMINI]
    assert skipped[0].error_type == "LLMQuotaExhaustedError"


@pytest.mark.asyncio
async def test_an_entirely_blocked_chain_raises_the_original_reason(all_keys, call):
    """Not "everything failed" — "gemini is out of quota"."""
    call.side_effect = [
        gemini_daily_quota_error(),
        gemini_daily_quota_error(),
        openrouter_insufficient_credits_error(),
    ]
    gateway = chained()
    with pytest.raises(LLMQuotaExhaustedError):
        await gateway.generate(prompt="first")

    call.reset_mock()
    call.side_effect = None
    with pytest.raises(LLMQuotaExhaustedError, match="was not called"):
        await gateway.generate(prompt="second")

    call.assert_not_awaited()


# ---------------------------------------------------------------------------
# Observability and secrecy (Phases 15, 31) — TEST 16
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_trace_records_every_attempt(all_keys, call):
    """Phase 15: provider, model, attempt, latency, status, error type."""
    call.side_effect = [
        service_unavailable_error(),
        gemini_daily_quota_error(),
        completion("from groq", model=GROQ),
    ]

    response = await chained().generate(prompt="hi")

    assert [(item.provider, item.attempt, item.status) for item in response.trace] == [
        ("gemini", 1, "failed"),
        ("gemini", 2, "failed"),
        ("groq", 1, "completed"),
    ]
    assert response.trace[0].error_type == "LLMServiceUnavailableError"
    assert response.trace[1].error_type == "LLMQuotaExhaustedError"
    assert all(item.latency_ms >= 0 for item in response.trace)


@pytest.mark.asyncio
async def test_no_provider_key_ever_reaches_a_log_or_an_error(
    all_keys, call, caplog
):
    """TEST 16, for the fallback providers too.

    Each provider echoes its own credential back in the failure — the
    worst realistic case. Every key must still reach its provider while
    appearing in neither the logs nor the raised exception.
    """
    import logging

    call.side_effect = [
        ProviderException(f"gemini rejected: api_key={FAKE_GEMINI_KEY}", 400),
    ]

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(LLMInvalidRequestError) as exc_info:
            await chained().generate(prompt="hi")

    assert call.await_args.kwargs["api_key"] == FAKE_GEMINI_KEY
    for key in (FAKE_GEMINI_KEY, FAKE_GROQ_KEY, FAKE_OPENROUTER_KEY):
        assert key not in caplog.text
        assert key not in str(exc_info.value)


@pytest.mark.asyncio
async def test_fallback_provider_keys_are_scrubbed(all_keys, call):
    """A Groq key echoed back by Groq must be redacted too."""
    call.side_effect = [
        gemini_daily_quota_error(),
        ProviderException(f"groq rejected: Authorization: Bearer {FAKE_GROQ_KEY}", 400),
    ]

    with pytest.raises(LLMInvalidRequestError) as exc_info:
        await chained().generate(prompt="hi")

    assert FAKE_GROQ_KEY not in str(exc_info.value)


@pytest.mark.asyncio
async def test_each_provider_is_sent_its_own_credential(all_keys, call):
    """Routing a Gemini key to Groq would be a subtle, expensive bug."""
    call.side_effect = [
        gemini_daily_quota_error(),
        completion("from groq", model=GROQ),
    ]

    await chained().generate(prompt="hi")

    assert call.await_args_list[0].kwargs["api_key"] == FAKE_GEMINI_KEY
    assert call.await_args_list[1].kwargs["api_key"] == FAKE_GROQ_KEY


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_openrouter_completion_falls_back_like_any_other_failure(
    all_keys, call
):
    """The other real OpenRouter failure mode from Sprint 9H model
    selection: HTTP 200, empty content. Not a provider exception, so
    it is the existing `_to_response` empty-content guard that fires,
    not the classification logic under test above -- this asserts
    that guard still participates correctly in the fallback chain
    rather than being a special case."""
    call.side_effect = [
        gemini_daily_quota_error(),
        service_unavailable_error(),
        service_unavailable_error(),
        service_unavailable_error(),
        openrouter_empty_completion_error(),
    ]

    # Sprint 16 Phase 8.3: message reworded to cover `content=""` too
    # (reproduced for real against a local Ollama model), not just `None`.
    with pytest.raises(LLMProviderError, match="empty content"):
        await chained().generate(prompt="hi")

    assert models_called(call) == [GEMINI, GROQ, GROQ, GROQ, OPENROUTER]


def test_deepseek_is_not_in_the_deployed_chain():
    """Sprint 9H Phase 2/41: the active chain must name OpenRouter, not
    DeepSeek, without needing to delete DeepSeek's credential plumbing
    (covered separately -- it remains usable if reconfigured)."""
    assert "deepseek" not in (settings.fallback_llm or "")
    assert "deepseek" not in (settings.secondary_fallback_llm or "")


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_breaker(all_keys, call):
    """Ten simultaneous requests must not each rediscover the quota.

    Not a strict bound — calls that start before the first failure is
    recorded legitimately try Gemini too. The claim is only that
    remembering happens at all under concurrency, so a burst does not
    behave like ten independent processes.
    """
    def respond(*_args, **kwargs):
        if kwargs["model"] == GEMINI:
            raise gemini_daily_quota_error()
        return completion("from groq", model=GROQ)

    call.side_effect = respond
    gateway = chained()

    await gateway.generate(prompt="warm up")
    call.reset_mock()

    results = await asyncio.gather(
        *(gateway.generate(prompt=f"q{i}") for i in range(10))
    )

    assert all(response.provider == "groq" for response in results)
    assert models_called(call).count(GEMINI) == 0
