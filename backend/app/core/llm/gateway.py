"""The LLM gateway: one place that talks to LiteLLM.

Design goals, in priority order:

1. **Containment.** `litellm` is imported here and nowhere else. Every
   caller works with `LLMMessage`/`LLMResponse` and the `LLMError`
   hierarchy, so swapping the routing layer — or adding Ollama, Groq,
   or DeepSeek — changes this module alone. A test asserts the
   containment holds across the whole `app` package.
2. **Secret safety.** The API key is read from `SecretStr` settings at
   the moment of the call and passed straight to LiteLLM. It is never
   logged, never placed on an exception, and never returned. Provider
   error text is scrubbed before it reaches a log or an `LLMError`,
   because upstream libraries have been known to echo credentials back
   in messages.
3. **Honest failures.** Provider problems become typed application
   exceptions. The gateway never invents a response, and never returns
   a partial result that looks successful.
4. **Resilience, in exactly one place** (Sprint 9G). Classification,
   bounded retry, provider fallback and the health breaker all live
   here. Nodes, services and routers must not add retries of their
   own: layered retry policies multiply, and three levels of "just
   three attempts" is twenty-seven calls to a provider that already
   said no.

Model identifiers are LiteLLM's `provider/model` strings (for example
`gemini/gemini-1.5-pro`), supplied by configuration rather than
hardcoded, so a model upgrade is an environment change.

## What one `complete()` call does

    for candidate in [primary, fallback, secondary fallback]:
        skip it if the breaker says it is quota-exhausted or down
        try it, retrying transient failures with jittered backoff
        stop at the first success
        stop entirely on a terminal error (bad credentials, bad request)

The chain is built from configuration and filtered by what is actually
credentialed, so an unconfigured fallback is skipped silently rather
than attempted and failed. Only the generation provider changes across
the chain — the messages, the evidence, and every downstream contract
are identical for each candidate, which is what makes a fallback
answer as trustworthy as a primary one.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import litellm
from litellm import aembedding, acompletion

from app.core.config import settings
from app.core.llm.errors import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMInvalidRequestError,
    LLMModelNotFoundError,
    LLMProviderError,
    LLMQuotaExhaustedError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
    is_retryable,
    is_terminal,
    scrub_secrets,
)
from app.core.llm.provider_health import (
    ProviderHealthRegistry,
    RedisProviderHealthRegistry,
    get_provider_health_registry,
    is_provider_configured,
)
from app.core.llm.usage import record_llm_usage
from app.core.logging.logger import get_logger

logger = get_logger(__name__)

# Re-exported so `from app.core.llm.gateway import LLMProviderError`
# keeps working now that the taxonomy lives in `errors.py`. The split
# was made when the errors became policy inputs rather than incidental
# detail; it should not have forced every importer to move.
__all__ = [
    "LLMAuthenticationError",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMEmbeddingResponse",
    "LLMError",
    "LLMGateway",
    "LLMInvalidRequestError",
    "LLMMessage",
    "LLMModelNotFoundError",
    "LLMProviderError",
    "LLMQuotaExhaustedError",
    "LLMRateLimitError",
    "LLMResponse",
    "LLMServiceUnavailableError",
    "LLMTimeoutError",
    "get_llm_gateway",
    "provider_of",
    "scrub_secrets",
]

#: Roles accepted in a conversation, matching the OpenAI-style schema
#: LiteLLM normalizes every provider onto.
MessageRole = Literal["system", "user", "assistant"]

#: LiteLLM accepts either prefix for Ollama; both need `api_base`
#: rather than an API key.
_OLLAMA_PROVIDERS: frozenset[str] = frozenset({"ollama", "ollama_chat"})

#: Texts per embedding request. A whole document in one call (a
#: textbook is ~3000 chunks) crashes Ollama's model runner mid-request
#: -- it answers 400 "connection refused" to its own tokenizer -- while
#: the same chunks in batches of 64 embed fine (verified against bge-m3).
_EMBED_BATCH_SIZE = 64

# -- 429 classification vocabulary (Sprint 9G) -------------------------
#
# Everything below exists to answer one question: is this 429 a
# per-minute limit that will clear on its own, or a quota that will not
# clear today? Getting it wrong in either direction is expensive —
# retrying a daily quota wastes the whole window, and giving up on a
# per-minute limit sends traffic to a fallback that was never needed.
#
# The vocabulary is derived from a real Gemini refusal captured on this
# deployment, not from documentation:
#
#     "code": 429, "status": "RESOURCE_EXHAUSTED"
#     "message": "You exceeded your current quota, please check your
#                 plan and billing details. ... limit: 20 ..."
#     "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
#     "retryDelay": "35s"
#
# The trap is in the *message*: Google uses that same "exceeded your
# current quota / check your plan and billing details" wording for the
# per-minute limit too. Matching on it would classify every transient
# rate limit as a spent daily quota and disable a working provider.
#
# Only the quota id names the window, so the window markers below are
# the discriminator and are checked before anything else.

#: Windows that refill continuously. Always retryable.
_SHORT_WINDOW_MARKERS: tuple[str, ...] = (
    "perminute",
    "per minute",
    "persecond",
    "per second",
    "perhour",
    "per hour",
)

#: Windows measured in days. Never retryable within one run.
_LONG_WINDOW_MARKERS: tuple[str, ...] = (
    "perday",
    "per day",
    "daily",
    "generate_content_free_tier_requests",
)

#: A spent prepaid balance or disabled billing, rather than a windowed
#: limit. Not retryable either: money does not appear during a backoff.
#: Wording taken from the OpenAI-compatible providers (Groq, DeepSeek).
_BALANCE_EXHAUSTED_MARKERS: tuple[str, ...] = (
    "insufficient_quota",
    "insufficient balance",
    "credit balance",
    "billing_not_active",
    "exceeded your monthly",
)

#: Text that says "this is a 429" without saying which kind. Used only
#: to route into `_classify_rate_limit`, never to decide the outcome —
#: Gemini reports both kinds as `RESOURCE_EXHAUSTED`.
_RATE_LIMIT_SIGNALS: tuple[str, ...] = (
    "rate limit",
    "ratelimit",
    "too many requests",
    "429",
    "resource_exhausted",
    "resource has been exhausted",
    "quota",
)

#: Markers for a provider that is up but temporarily unable to serve.
_UNAVAILABLE_MARKERS: tuple[str, ...] = (
    "503",
    "service unavailable",
    "serviceunavailable",
    "overloaded",
    "server had an error",
    "internal server error",
    "500",
    "502",
    "bad gateway",
    "try again later",
)

# LiteLLM is chatty by default and, on some providers, echoes the
# request (including auth headers) at debug level. Turn that off at
# import time so a stray log-level change can never surface a key.
litellm.suppress_debug_info = True
litellm.drop_params = True


@dataclass(frozen=True)
class LLMMessage:
    """One turn of a conversation, independent of any provider schema."""

    role: MessageRole
    content: str

    def as_dict(self) -> dict[str, str]:
        """Render in the OpenAI-style shape LiteLLM expects."""
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class LLMEmbeddingResponse:
    """The result of one `LLMGateway.embed()` call.

    Contains nothing provider-specific and nothing sensitive — in
    particular, never the input texts themselves, since those may be
    full document chunks and this response is safe to log.
    """

    vectors: list[list[float]]
    dimension: int
    model: str
    provider: str
    latency_ms: int


@dataclass(frozen=True)
class LLMAttempt:
    """One try against one model, successful or not (Sprint 9G).

    The unit of the observability trace. Carries no prompt text, no
    response text and no credential — only what an operator needs to
    reconstruct *how* an answer was obtained, which makes it safe to
    log and to persist in a research step.
    """

    model: str
    provider: str
    #: 1-based, counting retries against this model only. Resets for
    #: each candidate in the fallback chain.
    attempt: int
    #: "completed", "failed", or "skipped" (blocked by the breaker
    #: before any call was made).
    status: str
    latency_ms: int = 0
    error_type: str | None = None
    #: Truncated and already scrubbed by `LLMError`.
    error_message: str | None = None

    def as_dict(self) -> dict[str, object]:
        """A plain, JSON-safe dict for logs and the research trace."""
        return {
            "model": self.model,
            "provider": self.provider,
            "attempt": self.attempt,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class LLMResponse:
    """A completed generation, plus safe metadata about the call.

    Contains nothing provider-specific and nothing sensitive, so it is
    safe to log, persist, or return from an API.
    """

    content: str
    model: str
    provider: str
    latency_ms: int
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)

    # -- resilience metadata (Sprint 9G) ---------------------------
    #
    # Present on every response so a caller can always tell how the
    # answer was obtained. A fallback answer is a perfectly good answer,
    # but it must never be indistinguishable from a primary one — the
    # research trace has to be able to say which model actually spoke.
    #: Total provider calls made, across retries and fallbacks.
    attempts: int = 1
    #: True when the model that answered was not the first choice.
    fallback_used: bool = False
    #: The model the call started with. Equal to `model` on the happy
    #: path; different when a fallback answered.
    primary_model: str | None = None
    #: Why the primary was not used, when it was not. `None` on the
    #: happy path.
    primary_error_type: str | None = None
    #: Every attempt in order, including skipped and failed ones.
    trace: tuple[LLMAttempt, ...] = ()


def provider_of(model: str) -> str:
    """The provider segment of a LiteLLM model id.

    `gemini/gemini-1.5-pro` -> `gemini`. A bare model name has no
    explicit provider, which LiteLLM resolves by its own rules.
    """
    return model.split("/", 1)[0] if "/" in model else "default"


class LLMGateway:
    """Application-level client for text generation.

    Stateless and cheap to construct; `get_llm_gateway()` returns a
    shared instance. Configuration is read from `Settings` at call
    time, so the gateway honours whatever the environment supplies
    without needing to be rebuilt.
    """

    def __init__(
        self,
        *,
        default_model: str | None = None,
        fallback_model: str | None = None,
        secondary_fallback_model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_base_delay: float | None = None,
        retry_max_delay: float | None = None,
        provider_health: "ProviderHealthRegistry | RedisProviderHealthRegistry | None" = None,
    ) -> None:
        self._default_model = default_model or settings.default_llm
        # `is None` rather than `or`: an explicit `fallback_model=""`
        # means "no fallback", and must not fall through to the
        # configured value. With `or`, a caller asking for a
        # single-provider gateway silently got the deployment's whole
        # chain — which is exactly the kind of surprise that makes a
        # test pass for the wrong reason.
        self._fallback_model = _configured_model(
            settings.fallback_llm if fallback_model is None else fallback_model
        )
        self._secondary_fallback_model = _configured_model(
            settings.secondary_fallback_llm
            if secondary_fallback_model is None
            else secondary_fallback_model
        )
        self._temperature = temperature if temperature is not None else settings.llm_temperature
        self._max_tokens = max_tokens or settings.llm_max_tokens
        self._timeout = timeout or settings.llm_timeout
        # `is not None` rather than `or`, so `max_retries=0` means "no
        # retries" instead of silently falling back to the configured
        # value — the health probe depends on being able to ask for
        # exactly one attempt.
        self._max_retries = max_retries if max_retries is not None else settings.llm_max_retries
        self._retry_base_delay = (
            retry_base_delay if retry_base_delay is not None else settings.llm_retry_base_delay
        )
        self._retry_max_delay = (
            retry_max_delay if retry_max_delay is not None else settings.llm_retry_max_delay
        )
        # Injectable so tests get an isolated breaker; the shared
        # process-wide registry otherwise.
        self._health = provider_health or get_provider_health_registry()

    @property
    def default_model(self) -> str:
        """The model used when a caller does not name one."""
        return self._default_model

    @property
    def fallback_model(self) -> str | None:
        """The model tried when the primary one fails, if configured."""
        return self._fallback_model

    @property
    def secondary_fallback_model(self) -> str | None:
        """The model tried when the first fallback also fails, if configured."""
        return self._secondary_fallback_model

    async def generate(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        response_format: dict[str, Any] | None = None,
        think: bool | None = None,
        num_ctx: int | None = None,
        allow_fallback: bool = True,
        max_retries: int | None = None,
        respect_provider_health: bool = True,
    ) -> LLMResponse:
        """Generate a completion from a system/user prompt pair.

        The common case. `response_format` is passed through for
        providers that support structured output (for example
        `{"type": "json_object"}`); LiteLLM drops it for providers that
        do not, rather than failing the call. `think` and `num_ctx` are
        likewise pass-through generation settings for Ollama models;
        the gateway scopes them to Ollama so a local-model option cannot
        leak into a cloud fallback request.

        The three resilience arguments exist for the health probe,
        which must measure one specific model with one attempt and no
        substitution. Ordinary callers leave them alone.
        """
        messages: list[LLMMessage] = []
        if system_prompt:
            messages.append(LLMMessage(role="system", content=system_prompt))
        messages.append(LLMMessage(role="user", content=prompt))

        return await self.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
            think=think,
            num_ctx=num_ctx,
            allow_fallback=allow_fallback,
            max_retries=max_retries,
            respect_provider_health=respect_provider_health,
        )

    async def complete(
        self,
        *,
        messages: list[LLMMessage],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        response_format: dict[str, Any] | None = None,
        think: bool | None = None,
        num_ctx: int | None = None,
        allow_fallback: bool = True,
        max_retries: int | None = None,
        respect_provider_health: bool = True,
    ) -> LLMResponse:
        """Complete a conversation, surviving what can be survived.

        Walks the configured model chain — primary, fallback, secondary
        fallback — stopping at the first success. Within each candidate,
        transient failures are retried with bounded jittered backoff;
        between candidates, nothing is retried at all.

        Three kinds of failure end the whole call immediately, without
        touching a fallback: a missing credential, a rejected
        credential, and a request the provider refuses. All three would
        fail identically on every other provider, so trying more of them
        would only turn one clear error into three confusing ones.

        A spent daily quota, by contrast, moves straight to the fallback
        with no retry — the distinction this sprint was built around.
        """
        if not messages:
            raise LLMConfigurationError("At least one message is required.")

        target = model or self._default_model
        chain = self._model_chain(target, allow_fallback=allow_fallback)
        trace: list[LLMAttempt] = []
        primary_error_type: str | None = None
        last_error: LLMError | None = None

        for position, candidate in enumerate(chain):
            provider = provider_of(candidate)

            # Keyed by model, not provider: Gemini's daily quota is
            # per model, so one exhausted model must not disable
            # another on the same provider.
            if respect_provider_health and self._health.is_blocked(candidate):
                health = self._health.status(candidate)
                logger.info(
                    "llm_provider_skipped",
                    model=candidate,
                    provider=provider,
                    status=health.status.value,
                    retry_after_seconds=health.retry_after_seconds,
                    reason="provider_health",
                )
                trace.append(
                    LLMAttempt(
                        model=candidate,
                        provider=provider,
                        attempt=0,
                        status="skipped",
                        error_type=health.error_type,
                        error_message=health.error_message,
                    )
                )
                if position == 0:
                    primary_error_type = health.error_type
                # A blocked provider leaves no exception of its own, so
                # synthesise one for the case where the whole chain is
                # blocked and something has to be raised.
                last_error = last_error or _blocked_error(candidate, health)
                continue

            if position > 0:
                logger.warning(
                    "llm_fallback_engaged",
                    primary_model=chain[0],
                    fallback_model=candidate,
                    fallback_position=position,
                    error_type=primary_error_type,
                )

            try:
                response = await self._call_with_retries(
                    messages=messages,
                    model=candidate,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    response_format=response_format,
                    think=think,
                    num_ctx=num_ctx,
                    max_retries=(
                        self._max_retries if max_retries is None else max_retries
                    ),
                    trace=trace,
                )
            except LLMError as error:
                last_error = error
                if position == 0:
                    primary_error_type = type(error).__name__
                if is_terminal(error):
                    # Not a provider problem. Report it as-is rather
                    # than burning the fallback chain on a request that
                    # is wrong everywhere.
                    logger.error(
                        "llm_chain_aborted",
                        model=candidate,
                        provider=provider,
                        error_type=type(error).__name__,
                        reason="terminal_error",
                    )
                    raise
                continue

            record_llm_usage(
                provider=response.provider,
                model=response.model,
                input_tokens=response.prompt_tokens,
                output_tokens=response.completion_tokens,
            )
            return replace(
                response,
                attempts=sum(1 for item in trace if item.status != "skipped"),
                fallback_used=position > 0,
                primary_model=chain[0],
                primary_error_type=primary_error_type if position > 0 else None,
                trace=tuple(trace),
            )

        # Every candidate failed or was blocked. Raise the last real
        # failure rather than a generic wrapper, so the caller sees the
        # actual reason (quota, outage, bad model) instead of a summary.
        logger.error(
            "llm_chain_exhausted",
            models=chain,
            attempts=len(trace),
            error_type=type(last_error).__name__ if last_error else None,
        )
        raise last_error or LLMProviderError(
            "No LLM provider is configured or available.", model=target
        )

    # -- chain and retry ---------------------------------------------

    def _model_chain(self, target: str, *, allow_fallback: bool) -> list[str]:
        """The models to try, in order.

        The primary is always included, even when its credentials are
        missing: a misconfigured primary must surface as a clear
        `LLMConfigurationError`, not be quietly skipped in favour of a
        fallback nobody asked for. Fallbacks are the opposite — an
        unconfigured one is dropped here, so an optional provider
        without an API key costs nothing and breaks nothing.
        """
        chain = [target]
        if not allow_fallback:
            return chain

        for candidate in (self._fallback_model, self._secondary_fallback_model):
            if not candidate or candidate in chain:
                continue
            provider = provider_of(candidate)
            if not is_provider_configured(provider):
                logger.info(
                    "llm_fallback_unconfigured",
                    model=candidate,
                    provider=provider,
                    reason="missing_credentials",
                )
                continue
            chain.append(candidate)
        return chain

    async def _call_with_retries(
        self,
        *,
        messages: list[LLMMessage],
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        timeout: float | None,
        response_format: dict[str, Any] | None,
        think: bool | None,
        num_ctx: int | None,
        max_retries: int,
        trace: list[LLMAttempt],
    ) -> LLMResponse:
        """Call one model, retrying only what is worth retrying.

        Hand-rolled rather than delegated to `tenacity` (already a
        dependency): the decision to retry depends on the error's place
        in the taxonomy, every attempt has to be recorded in the trace,
        and the provider health registry has to be updated per attempt.
        Expressing all three through a retry decorator's hooks would be
        more indirection than the fifteen lines it replaces.
        """
        provider = provider_of(model)
        attempts = max_retries + 1

        for attempt in range(1, attempts + 1):
            start = time.monotonic()
            try:
                response = await self._call(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    response_format=response_format,
                    think=think,
                    num_ctx=num_ctx,
                    attempt=attempt,
                )
            except LLMError as error:
                latency_ms = int((time.monotonic() - start) * 1000)
                self._health.record_failure(model, error=error)
                trace.append(
                    LLMAttempt(
                        model=model,
                        provider=provider,
                        attempt=attempt,
                        status="failed",
                        latency_ms=latency_ms,
                        error_type=type(error).__name__,
                        error_message=str(error)[:500],
                    )
                )
                if attempt >= attempts or not is_retryable(error):
                    raise

                delay = self._retry_delay(attempt)
                logger.warning(
                    "llm_retry_scheduled",
                    model=model,
                    provider=provider,
                    attempt=attempt,
                    remaining=attempts - attempt,
                    delay_seconds=round(delay, 3),
                    error_type=type(error).__name__,
                )
                await asyncio.sleep(delay)
                continue

            self._health.record_success(model)
            trace.append(
                LLMAttempt(
                    model=model,
                    provider=provider,
                    attempt=attempt,
                    status="completed",
                    latency_ms=response.latency_ms,
                )
            )
            return response

        # `attempts >= 1` always, so the loop either returns or raises.
        raise LLMProviderError("Retry loop ended without a result.", model=model)

    def _retry_delay(self, attempt: int) -> float:
        """Seconds to wait before retry number `attempt` (1-based).

        Exponential with *equal* jitter: half the backoff window is
        fixed and half is random. Full jitter (uniform over the whole
        window) is the more common recipe, but it can produce a delay
        near zero, which turns the first retry into an immediate repeat
        of a call the provider just rejected.

        Provider-supplied retry hints are deliberately ignored. The live
        Gemini quota refusal that motivated this sprint carried
        `retryDelay: "35s"` on a quota that resets once a day — a hint
        that would have produced a polite infinite loop. Our own bounded
        schedule cannot be talked into that.
        """
        window = min(self._retry_base_delay * (2 ** (attempt - 1)), self._retry_max_delay)
        return window / 2 + random.uniform(0, window / 2)

    async def embed(
        self,
        *,
        texts: list[str],
        model: str,
        timeout: float | None = None,
    ) -> LLMEmbeddingResponse:
        """Embed a batch of texts. No system/user prompt shape here —
        embeddings are a distinct LiteLLM operation (`aembedding`, not
        `acompletion`), so this does not go through `complete()`.

        No fallback model: an embedding from a different model has a
        different vector space, and silently mixing spaces in one
        pgvector column would make similarity search meaningless.

        `texts` is never logged — chunk content flows through this
        method and log volume/content is exactly what
        `app.core.llm`'s Phase 10 constraints ask to avoid.
        """
        if not texts:
            raise LLMConfigurationError("At least one text is required to embed.")

        provider = provider_of(model)
        request: dict[str, Any] = {
            "model": model,
            "timeout": timeout or self._timeout,
        }

        # Same credential resolution `complete()` performs. Embeddings
        # only ever ran against Ollama, which needs no key, so this path
        # never needed one — a hosted embedding model (Jina AI) does,
        # and without it LiteLLM sends an unauthenticated request that
        # comes back AUTH_MISSING_API_KEY.
        api_key = self._credentials_for(provider, model)
        if api_key is not None:
            request["api_key"] = api_key
        if provider in _OLLAMA_PROVIDERS:
            request["api_base"] = settings.ollama_base_url

        logger.info(
            "llm_embedding_request_started",
            model=model,
            provider=provider,
            text_count=len(texts),
            timeout=request["timeout"],
        )

        # Sent in `_EMBED_BATCH_SIZE` slices, in order; the timeout applies
        # per request. Any failing slice fails the whole call, so a caller
        # never gets vectors for only part of its texts.
        start = time.monotonic()
        results: list[tuple[Any, int]] = []
        try:
            for batch_start in range(0, len(texts), _EMBED_BATCH_SIZE):
                batch = texts[batch_start : batch_start + _EMBED_BATCH_SIZE]
                results.append((await aembedding(**request, input=batch), len(batch)))
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            error = self._normalize_error(exc, model=model)
            logger.error(
                "llm_embedding_request_failed",
                model=model,
                provider=provider,
                latency_ms=latency_ms,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise error from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        vectors = [
            vector
            for result, count in results
            for vector in self._to_embedding_vectors(result, model=model, expected_count=count)
        ]
        # Each batch is checked on its own above; batches must also agree.
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) > 1:
            raise LLMProviderError(
                f"The provider returned inconsistent vector dimensions: {sorted(dimensions)}.",
                model=model,
            )
        dimension = len(vectors[0]) if vectors else 0
        logger.info(
            "llm_embedding_request_completed",
            model=model,
            provider=provider,
            latency_ms=latency_ms,
            vector_count=len(vectors),
            dimension=dimension,
        )
        return LLMEmbeddingResponse(
            vectors=vectors,
            dimension=dimension,
            model=getattr(results[-1][0], "model", None) or model,
            provider=provider,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _to_embedding_vectors(
        result: Any, *, model: str, expected_count: int
    ) -> list[list[float]]:
        """Extract and validate embedding vectors from a LiteLLM response.

        Defensive for the same reason `_to_response` is: a provider
        returning a malformed or short batch must surface as a typed
        error, never as a silently truncated or reordered result.
        """
        data = getattr(result, "data", None) or []
        if len(data) != expected_count:
            raise LLMProviderError(
                f"Expected {expected_count} embedding(s), got {len(data)}.", model=model
            )

        vectors: list[list[float]] = []
        for item in data:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not vector or not isinstance(vector, list):
                raise LLMProviderError(
                    "The provider returned an item with no embedding vector.", model=model
                )
            vectors.append([float(value) for value in vector])

        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) > 1:
            raise LLMProviderError(
                f"The provider returned inconsistent vector dimensions: {sorted(dimensions)}.",
                model=model,
            )
        return vectors

    async def _call(
        self,
        *,
        messages: list[LLMMessage],
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        timeout: float | None,
        response_format: dict[str, Any] | None,
        think: bool | None = None,
        num_ctx: int | None = None,
        attempt: int = 1,
    ) -> LLMResponse:
        """Perform one LiteLLM call and normalize its result.

        The single provider round trip. Retry, fallback and health
        bookkeeping all live in the callers above, so this stays the
        one honest measurement: it either returns what a provider
        actually said, or raises a typed error explaining why it did
        not.
        """
        provider = provider_of(model)
        if provider == "ollama" and response_format is not None:
            # Sprint 16 Phase 8.3: LiteLLM's plain `ollama` provider calls
            # Ollama's `/api/generate` endpoint, whose JSON-mode transform
            # reads only the raw `response` field. A hybrid-reasoning model
            # (e.g. qwen3.5) asked for JSON puts its entire answer in the
            # separate `thinking` field instead and leaves `response`
            # empty -- LiteLLM then hands back "success" with blank
            # content (reproduced directly against a real local model:
            # `eval_count` > 0, `done_reason: stop`, `response: ""`).
            # `ollama_chat` calls `/api/chat`, which correctly separates
            # `message.content` from `message.thinking` regardless of
            # reasoning mode -- confirmed against the same model with the
            # same prompt. Rewriting only when a response_format is
            # requested, since that's the reproduced failure; a caller
            # that already wrote `ollama_chat/...` is left untouched.
            model = "ollama_chat/" + model.split("/", 1)[1]
            provider = "ollama_chat"
            if think is None:
                # A short "say hello"-sized prompt reproduces cleanly with
                # `ollama_chat` alone. A real 30-page document under the
                # full target JSON schema did not: the model's `<think>`
                # pass consumed the entire token budget and `content`
                # was still "" at `done_reason=stop` -- confirmed by
                # forcing `think: false` on the same real document and
                # schema, which returned real, complete JSON in 15s.
                # Only defaulted when the caller has no opinion; an
                # explicit `think=True` is still honoured.
                think = False
        request: dict[str, Any] = {
            "model": model,
            "messages": [message.as_dict() for message in messages],
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self._max_tokens,
            "timeout": timeout or self._timeout,
        }

        api_key = self._credentials_for(provider, model)
        if api_key is not None:
            request["api_key"] = api_key
        if provider in _OLLAMA_PROVIDERS:
            # Ollama needs no key, only where to find the server. Read
            # here rather than baked into the request builder above, so
            # a caller-supplied `model` (not just the configured
            # default) still routes to the right host.
            request["api_base"] = settings.ollama_base_url
        if response_format is not None:
            request["response_format"] = response_format
        if think is not None and provider in _OLLAMA_PROVIDERS:
            # `think` is Ollama's hybrid-reasoning toggle (Sprint 12.5),
            # not a general completion parameter. `litellm.drop_params`
            # strips *recognized* optional params a provider doesn't
            # support, but `think` is an arbitrary custom key that
            # LiteLLM passes straight through as extra body -- forwarded
            # unscoped, it reached Groq's OpenAI-compatible endpoint on
            # every fallback attempt and was rejected as an unsupported
            # property (Sprint 12.6 Phase 2/3 finding). A caller asking
            # only Ollama to skip its reasoning pass must not also ask
            # every fallback provider to do the same.
            request["think"] = think
        if num_ctx is not None and provider in _OLLAMA_PROVIDERS:
            # Ollama's context window is request-specific. Like `think`,
            # it must stay off cloud-provider fallback requests.
            request["num_ctx"] = num_ctx

        # Logged without the request payload or the key: prompts can
        # carry user data and `request` carries the credential.
        logger.info(
            "llm_request_started",
            model=model,
            provider=provider,
            attempt=attempt,
            message_count=len(messages),
            temperature=request["temperature"],
            max_tokens=request["max_tokens"],
            timeout=request["timeout"],
        )

        start = time.monotonic()
        try:
            completion = await acompletion(**request)
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            error = self._normalize_error(exc, model=model)
            logger.error(
                "llm_request_failed",
                model=model,
                provider=provider,
                attempt=attempt,
                latency_ms=latency_ms,
                error_type=type(error).__name__,
                error_message=str(error),
            )
            raise error from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        response = self._to_response(
            completion, model=model, provider=provider, latency_ms=latency_ms
        )
        logger.info(
            "llm_request_completed",
            model=response.model,
            provider=response.provider,
            attempt=attempt,
            latency_ms=response.latency_ms,
            finish_reason=response.finish_reason,
            total_tokens=response.total_tokens,
            content_characters=len(response.content),
        )
        return response

    @staticmethod
    def _credentials_for(provider: str, model: str) -> str | None:
        """Return the API key for a provider, or None if it needs none.

        The single point where a secret is read. The value is handed
        directly to LiteLLM and never stored, logged, or returned.

        A cloud provider with no key raises rather than proceeding: the
        alternative is a network round trip that can only end in a 401,
        and an error message about credentials is more useful than one
        about authentication. Fallback candidates never reach this
        branch — `_model_chain` filters unconfigured providers out
        first, so an optional provider without a key is skipped, not
        failed.
        """
        secret = _PROVIDER_CREDENTIALS.get(provider)
        if secret is None:
            # Locally hosted (Ollama) or a provider LiteLLM resolves
            # from its own environment conventions. Nothing to read.
            return None

        env_name, has_credentials, read = secret
        if not has_credentials():
            raise LLMConfigurationError(
                f"{env_name} is not configured; set it in the environment "
                f"before calling a {provider}/* model.",
                model=model,
            )
        return read()

    @staticmethod
    def _to_response(
        completion: Any, *, model: str, provider: str, latency_ms: int
    ) -> LLMResponse:
        """Convert a LiteLLM completion into an application response.

        Defensive about shape: LiteLLM normalizes across providers, but
        a provider returning no choices must surface as a typed error
        rather than an `IndexError` or a silently empty answer.
        """
        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise LLMProviderError(
                "The provider returned no completion choices.", model=model
            )

        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        # Sprint 16 Phase 8.3: `content is None` alone let a real failure
        # through -- a local reasoning model in JSON mode returned
        # `content=""` (not None) with `finish_reason="stop"` and a
        # nonzero token count, which read as a normal, successful,
        # blank answer. Blank is never a valid answer for this gateway's
        # callers, so it is a failure here too, not just an absent field.
        if not content or not content.strip():
            raise LLMProviderError(
                "The provider returned a choice with empty content.", model=model
            )

        usage = getattr(completion, "usage", None)
        usage_dict: dict[str, Any] = {}
        if usage is not None:
            usage_dict = (
                usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
            )

        return LLMResponse(
            content=content,
            # The provider may report a more specific model id than the
            # alias that was requested; prefer what actually served it.
            model=getattr(completion, "model", None) or model,
            provider=provider,
            latency_ms=latency_ms,
            finish_reason=getattr(choices[0], "finish_reason", None),
            prompt_tokens=usage_dict.get("prompt_tokens"),
            completion_tokens=usage_dict.get("completion_tokens"),
            total_tokens=usage_dict.get("total_tokens"),
            raw_usage=usage_dict,
        )

    @staticmethod
    def _normalize_error(exc: Exception, *, model: str) -> LLMError:
        """Map a LiteLLM exception onto the gateway's error hierarchy.

        Matches on LiteLLM's exception classes where they exist and
        falls back to the message text, so a version bump that renames
        an exception degrades to a generic provider error rather than
        escaping the hierarchy entirely.

        Order matters, and each position below is load-bearing:

        1. **Model-not-found before connection errors.** Ollama reports
           a missing model as the *same* LiteLLM class
           (`APIConnectionError`) it uses for a genuinely unreachable
           server — confirmed live against this deployment — so only
           the message body tells them apart.
        2. **Quota/rate limit before the generic provider error**
           (Sprint 9G). Both arrive as HTTP 429; separating them is the
           point of this sprint, and `_classify_rate_limit` reads the
           provider's own quota metadata to decide.
        3. **Timeouts before authentication.** A timeout message can
           mention the endpoint path, and some provider errors mention
           "permission" incidentally; checking the stronger signal
           first keeps the weaker text heuristics from stealing a case.
        """
        if isinstance(exc, LLMError):
            return exc

        message = scrub_secrets(str(exc))
        lowered = message.lower()
        status_code = _status_code_of(exc)

        # OpenRouter (Sprint 9H) reports a spent prepaid balance as
        # HTTP 402 Payment Required — documented in OpenRouter's own
        # API reference, not reproduced live in this deployment (the
        # configured key is a fresh free-tier key with $0 usage, so it
        # has never actually run out). Checked ahead of everything
        # else for the same reason DeepSeek's 400 balance error is
        # checked early: a credit exhaustion must route to the
        # fallback, never abort the chain as if the request itself
        # were malformed.
        if status_code == 402:
            return LLMQuotaExhaustedError(
                f"The provider's quota is exhausted: {message}", model=model
            )

        if "model" in lowered and "not found" in lowered:
            return LLMModelNotFoundError(
                f"The configured model is not available: {message}", model=model
            )
        if _is_litellm_instance(exc, "NotFoundError") or status_code == 404:
            return LLMModelNotFoundError(
                f"The configured model is not available: {message}", model=model
            )

        # Deliberately excludes `APIConnectionError`: that class covers
        # both timeouts-that-surface-as-connection-drops and genuine
        # unreachable-server failures, which the checks below tell
        # apart. Lumping it in here (as earlier code did) would
        # misclassify every connection failure as a timeout.
        if isinstance(exc, TimeoutError) or _is_litellm_instance(exc, "Timeout"):
            return LLMTimeoutError(f"The model did not respond in time: {message}", model=model)
        if "timed out" in lowered or "timeout" in lowered:
            return LLMTimeoutError(f"The model did not respond in time: {message}", model=model)

        if (
            _is_litellm_instance(exc, "AuthenticationError")
            or _is_litellm_instance(exc, "PermissionDeniedError")
            or status_code in (401, 403)
        ):
            return LLMAuthenticationError(
                f"The provider rejected the credentials: {message}", model=model
            )
        if "api key" in lowered or "unauthorized" in lowered or "permission" in lowered:
            return LLMAuthenticationError(
                f"The provider rejected the credentials: {message}", model=model
            )

        # Sprint 9G: the 429 fork. Checked before the generic 400
        # branch because some providers report a rate limit with a
        # 4xx-shaped exception class.
        if _is_litellm_instance(exc, "RateLimitError") or status_code == 429:
            return _classify_rate_limit(message, model=model)

        # A spent prepaid balance, checked before the 400 branch
        # because of a case observed live on this deployment: DeepSeek
        # reports `{"message": "Insufficient Balance", "code":
        # "invalid_request_error"}` as HTTP 400. Read literally that is
        # a malformed request — terminal, no fallback — which would let
        # one unfunded provider abort a chain that had working
        # providers left in it. It is a quota problem wearing a 400.
        if _has_marker(lowered, _BALANCE_EXHAUSTED_MARKERS):
            return LLMQuotaExhaustedError(
                f"The provider's quota is exhausted: {message}", model=model
            )

        if (
            _is_litellm_instance(exc, "BadRequestError")
            or _is_litellm_instance(exc, "UnprocessableEntityError")
            or status_code in (400, 422)
        ):
            return LLMInvalidRequestError(
                f"The provider rejected the request: {message}", model=model
            )

        # Connection failures are checked BEFORE the 5xx branch, and
        # the order was earned: LiteLLM stamps `status_code = 500` on
        # `APIConnectionError`, so a 5xx-first check silently reported
        # "Ollama is not running" as "the provider is overloaded".
        # Observed live when the host's Ollama was down and every
        # embedding call came back as a service outage. Both are
        # retryable, so the behaviour was right and only the diagnosis
        # was wrong — which is the kind of wrong that costs an hour of
        # looking in the wrong place.
        if _is_litellm_instance(exc, "APIConnectionError"):
            return LLMConnectionError(
                f"Could not reach the model provider: {message}", model=model
            )
        if (
            "cannot connect" in lowered
            or "connection refused" in lowered
            or "connect call failed" in lowered
        ):
            return LLMConnectionError(
                f"Could not reach the model provider: {message}", model=model
            )

        if (
            _is_litellm_instance(exc, "ServiceUnavailableError")
            or _is_litellm_instance(exc, "InternalServerError")
            or (status_code is not None and status_code >= 500)
        ):
            return LLMServiceUnavailableError(
                f"The provider is temporarily unavailable: {message}", model=model
            )

        # Text-only fallbacks, for providers (and test doubles) that
        # raise a plain exception with no status code attached.
        if any(marker in lowered for marker in _RATE_LIMIT_SIGNALS):
            return _classify_rate_limit(message, model=model)
        if any(marker in lowered for marker in _UNAVAILABLE_MARKERS):
            return LLMServiceUnavailableError(
                f"The provider is temporarily unavailable: {message}", model=model
            )

        return LLMProviderError(message, model=model)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _is_litellm_instance(exc: Exception, name: str) -> bool:
    """Whether `exc` is an instance of LiteLLM's exception named `name`.

    Looked up by name at call time rather than imported: LiteLLM has
    moved these classes between `litellm` and `litellm.exceptions`
    across releases, and a missing class should degrade this check to
    `False` — leaving the text heuristics to classify — rather than
    breaking the import of the whole gateway.
    """
    candidates = tuple(
        candidate
        for candidate in (
            getattr(litellm, name, None),
            getattr(litellm.exceptions, name, None),
        )
        if isinstance(candidate, type) and issubclass(candidate, BaseException)
    )
    return bool(candidates) and isinstance(exc, candidates)


def _has_marker(message: str, markers: tuple[str, ...]) -> bool:
    """Whether any marker appears in `message`, ignoring separators.

    Comparing on alphanumerics only means `PerDay`, `per-day` and
    `per day` all match one marker, so the vocabulary does not need a
    spelling variant per provider.
    """
    compact = "".join(character for character in message.lower() if character.isalnum())
    return any(
        "".join(character for character in marker if character.isalnum()) in compact
        for marker in markers
    )


def _status_code_of(exc: Exception) -> int | None:
    """The HTTP status a provider exception carries, if any."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    if isinstance(status, str) and status.isdigit():
        return int(status)
    return None


def _classify_rate_limit(message: str, *, model: str) -> LLMError:
    """Split a 429 into "come back in a moment" and "come back tomorrow".

    Google returns both as `RESOURCE_EXHAUSTED` with status 429; the
    difference is only visible in the quota metadata. The daily variant
    observed live on this deployment reads::

        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        "quotaValue": "20"
        "retryDelay": "35s"

    Note the retry hint: 35 seconds, on a quota that resets once a day.
    Anything that trusted it would retry forever. So the window named
    in the quota id decides, and the retry hint is ignored.

    Precedence, strictly in this order:

    1. A short window (`PerMinute`, `PerHour`) wins outright. It is the
       only signal that is never ambiguous, and Google attaches its
       billing boilerplate to these too — so checking the boilerplate
       first would misread every transient limit as a spent day.
    2. A long window (`PerDay`) means the quota is gone for the day.
    3. A spent prepaid balance means it is gone until someone pays.
    4. Anything else is treated as a rate limit.

    Step 4 is where ambiguity lands, and it lands there on purpose.
    Guessing "quota exhausted" would disable a working provider for an
    hour on the strength of a phrase match; guessing "rate limited"
    costs at most a few bounded retries before the fallback runs
    anyway. The recoverable mistake wins.
    """
    if _has_marker(message, _SHORT_WINDOW_MARKERS):
        return LLMRateLimitError(
            f"The provider is rate limiting: {message}", model=model
        )
    if _has_marker(message, _LONG_WINDOW_MARKERS) or _has_marker(
        message, _BALANCE_EXHAUSTED_MARKERS
    ):
        return LLMQuotaExhaustedError(
            f"The provider's quota is exhausted: {message}", model=model
        )
    return LLMRateLimitError(f"The provider is rate limiting: {message}", model=model)


def _configured_model(value: str | None) -> str | None:
    """Normalize a configured model id, treating blank as "not set".

    `FALLBACK_LLM=` in `.env` must mean "no fallback" rather than a
    call to a model whose name is the empty string.
    """
    if not value:
        return None
    return value.strip() or None


def _blocked_error(model: str, health: Any) -> LLMError:
    """The error to raise when a model was skipped, not called.

    Reconstructed from the recorded state so the caller sees the
    original reason — "quota exhausted" rather than "everything
    failed". The message says explicitly that no request was made, so
    nobody reading a log mistakes a short-circuit for a fresh refusal.
    """
    from app.core.llm.provider_health import ProviderStatus

    detail = health.error_message or health.status.value
    summary = (
        f"{model} was not called: it is marked "
        f"{health.status.value} and is in cooldown for another "
        f"{round(health.retry_after_seconds or 0)}s. Last error: {detail}"
    )
    if health.status is ProviderStatus.QUOTA_EXHAUSTED:
        return LLMQuotaExhaustedError(summary, model=model)
    if health.status is ProviderStatus.RATE_LIMITED:
        return LLMRateLimitError(summary, model=model)
    return LLMServiceUnavailableError(summary, model=model)


#: Provider -> (env var name, "is it set?", "read it"). The single map
#: from a LiteLLM provider prefix to a credential, so adding a provider
#: is one entry rather than another branch in `_credentials_for`.
#: Values are read through `settings` at call time and never cached.
_PROVIDER_CREDENTIALS: dict[str, tuple[str, Any, Any]] = {
    "gemini": (
        "GEMINI_API_KEY",
        lambda: settings.has_gemini_credentials,
        lambda: settings.gemini_api_key.get_secret_value(),  # type: ignore[union-attr]
    ),
    "groq": (
        "GROQ_API_KEY",
        lambda: settings.has_groq_credentials,
        lambda: settings.groq_api_key.get_secret_value(),  # type: ignore[union-attr]
    ),
    "deepseek": (
        "DEEPSEEK_API_KEY",
        lambda: settings.has_deepseek_credentials,
        lambda: settings.deepseek_api_key.get_secret_value(),  # type: ignore[union-attr]
    ),
    "openrouter": (
        "OPENROUTER_API_KEY",
        lambda: settings.has_openrouter_credentials,
        lambda: settings.openrouter_api_key.get_secret_value(),  # type: ignore[union-attr]
    ),
    #: Embeddings only (`jina_ai/jina-embeddings-v3`) — registered so a
    #: missing key fails with a credential error at the call site rather
    #: than a 401 from LiteLLM's own env-var resolution.
    "jina_ai": (
        "JINA_API_KEY",
        lambda: settings.has_jina_credentials,
        lambda: settings.jina_api_key.get_secret_value(),  # type: ignore[union-attr]
    ),
}


async def reset_litellm_logging_worker_for_task_boundary() -> None:
    """Stop and clear LiteLLM's process-global async logging worker.

    Root cause (Sprint 9J): `litellm.litellm_core_utils.logging_worker.
    GLOBAL_LOGGING_WORKER` is a module-level singleton. Its internal
    `asyncio.Queue` and worker `Task` bind to whichever event loop is
    running the first time an async LiteLLM call completes, and stay
    bound to it for the object's lifetime — but every Celery task in
    `app.workers.tasks` runs its own `asyncio.run()`, a fresh loop
    closed the moment the task returns. Reproduced live against the
    real worker (Sprint 9J Phase 2): the *second* task to make an
    LLM/embedding call in a worker process's lifetime inherits a queue
    still bound to the *first* task's already-closed loop, and every
    `await self._queue.get()` in the freshly-spawned worker task then
    raises `RuntimeError: <Queue> is bound to a different event loop`
    — logged by asyncio as "Task exception was never retrieved" once
    garbage collected, since nothing awaits that abandoned worker task.

    Safe to discard entirely: AIKDAP registers no LiteLLM
    success/failure callbacks anywhere (`litellm.success_callback` and
    friends are never set in this codebase), so this worker exists only
    to run LiteLLM's own internal callback bookkeeping for callbacks
    that do not exist here. Our own observability is the synchronous
    `logger.info(...)` calls already in this module, which run
    unconditionally and do not depend on this worker's state at all.

    Reaches into the private `_queue` attribute because `LoggingWorker`
    has no public reset: `.stop()` alone cancels the worker *task* but
    leaves the stale *queue* object in place, so the next task's fresh
    worker task would bind to the old loop's queue and fail identically
    (confirmed live — calling `.stop()` without also clearing `_queue`
    does not remove the warning).

    Lives here, not in `app.workers.tasks`, so that module never has to
    import `litellm` directly — this is the one place that does, and
    `test_litellm_is_imported_only_by_the_gateway` holds unmodified.
    Called once at the end of every Celery task's event loop, win or
    lose, via `app.workers.tasks._run_task_loop`; a task that made no
    LiteLLM call at all hits a no-op `.stop()` on an uninitialized
    worker, cheap enough not to special-case.
    """
    from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

    await GLOBAL_LOGGING_WORKER.stop()
    GLOBAL_LOGGING_WORKER._queue = None  # noqa: SLF001 - see docstring


_gateway: LLMGateway | None = None


def get_llm_gateway() -> LLMGateway:
    """Return the shared gateway instance.

    A module-level singleton rather than a FastAPI dependency: the
    gateway is stateless and is called from Celery workers and internal
    services as well as from request handlers.
    """
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
