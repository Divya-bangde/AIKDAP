"""Sprint 9H: provider health shared across processes via Redis.

Runs against the REAL Redis this deployment already depends on
(`CELERY_BROKER_URL` in the test container, the same instance Celery
uses) rather than a fake — the property under test is literally
"do two independent `RedisProviderHealthRegistry` instances see the
same state through Redis", which a fake client cannot demonstrate.
That is also why this file is not offline the way `test_llm_resilience.py`
is: it is integration-shaped by necessity, matching this project's
existing convention of testing real infrastructure directly (see
`tests/conftest.py`'s database fixtures).

The degradation tests (Redis unreachable) inject a broken client
directly rather than stopping the real Redis container, so they run
without touching shared infrastructure other tests depend on.
"""

import time
import os

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.core.llm.errors import (
    LLMAuthenticationError,
    LLMQuotaExhaustedError,
    LLMRateLimitError,
)
from app.core.llm.provider_health import (
    REDIS_KEY_PREFIX,
    ProviderStatus,
    RedisProviderHealthRegistry,
)

GEMINI = "gemini/gemini-flash-latest"
GROQ = "groq/openai/gpt-oss-120b"
pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_disposable_redis():
    if not os.environ.get("TEST_REDIS_URL"):
        pytest.skip("requires disposable Redis via TEST_REDIS_URL")


class BrokenRedisClient:
    """A stand-in for a Redis client that cannot connect.

    Every method raises the same kind of error a real dropped
    connection would, so `RedisProviderHealthRegistry`'s `except`
    blocks are exercised the way they would be against a genuinely
    unreachable Redis, without needing to stop the real container.
    """

    def _fail(self, *_args, **_kwargs):
        raise ConnectionError("Redis is not reachable (simulated).")

    get = _fail
    setex = _fail
    scan_iter = _fail
    delete = _fail


@pytest.fixture
def redis_registry():
    """A real `RedisProviderHealthRegistry` against the real Redis,
    cleaned up before and after so tests do not see each other's keys."""
    if not os.environ.get("TEST_REDIS_URL"):
        pytest.skip("requires disposable Redis via TEST_REDIS_URL")
    registry = RedisProviderHealthRegistry(redis_url=settings.celery_broker_url)
    try:
        registry._get_client().ping()
    except Exception:
        pytest.fail("Disposable Redis unavailable (bounded client timeout)", pytrace=False)
    registry.reset()
    yield registry
    registry.reset()


@pytest.fixture
def broken_registry():
    """A registry whose Redis client always fails, to exercise the
    fallback path deterministically rather than by chance timing."""
    return RedisProviderHealthRegistry(client=BrokenRedisClient())


# ---------------------------------------------------------------------------
# Phase 8/9 — key format and stored value
# ---------------------------------------------------------------------------


def test_the_redis_key_matches_the_documented_format(redis_registry):
    """`aikdap:llm:health:<model>` — exactly, not a hash or an escape."""
    redis_registry.record_success(GEMINI)

    client = redis_registry._get_client()
    assert client.exists(f"{REDIS_KEY_PREFIX}{GEMINI}") == 1


def test_the_stored_value_has_no_secret_and_no_content(redis_registry):
    """Phase 9: status/provider/model/timestamp/error only. Never a
    key, a prompt, an answer, or a citation."""
    redis_registry.record_failure(
        GEMINI, error=LLMQuotaExhaustedError("spent, api_key=AIzaFAKEKEYSHOULDNEVERAPPEAR")
    )

    client = redis_registry._get_client()
    raw = client.get(f"{REDIS_KEY_PREFIX}{GEMINI}")
    assert raw is not None
    assert "AIzaFAKEKEYSHOULDNEVERAPPEAR" not in raw

    import json

    data = json.loads(raw)
    assert set(data) == {"status", "provider", "model", "updated_at", "error_type", "error_message"}
    assert data["status"] == "quota_exhausted"
    assert data["provider"] == "gemini"
    assert data["model"] == GEMINI


# ---------------------------------------------------------------------------
# Phase 10 — TTL
# ---------------------------------------------------------------------------


def test_a_blocking_status_is_written_with_ttl(redis_registry):
    """Phase 37: inspect Redis directly and see a TTL, not a permanent key."""
    redis_registry.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))

    client = redis_registry._get_client()
    ttl = client.ttl(f"{REDIS_KEY_PREFIX}{GEMINI}")
    assert 0 < ttl <= settings.llm_quota_cooldown_seconds + 1


def test_a_healthy_status_still_carries_a_hygiene_ttl(redis_registry):
    """Phase 10: 'do not create permanent dead-provider state' applies
    to a permanently-cached HEALTHY entry too — nothing lives forever."""
    redis_registry.record_success(GEMINI)

    client = redis_registry._get_client()
    ttl = client.ttl(f"{REDIS_KEY_PREFIX}{GEMINI}")
    assert 0 < ttl <= settings.llm_health_state_ttl_seconds + 1


def test_the_key_actually_expires_and_the_provider_becomes_eligible_again(
    redis_registry, monkeypatch
):
    """Phase 25/37, end to end: shrink the cooldown, let real time pass
    it, and confirm both the read-time decision AND the physical Redis
    key reflect it."""
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-cooldown-key"))
    monkeypatch.setattr(settings, "llm_quota_cooldown_seconds", 0.05)
    redis_registry.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))
    assert redis_registry.is_blocked(GEMINI) is True

    time.sleep(0.15)

    assert redis_registry.is_blocked(GEMINI) is False
    assert redis_registry.status(GEMINI).status is ProviderStatus.CONFIGURED


# ---------------------------------------------------------------------------
# Phase 12/25 — cross-process sharing (the headline acceptance test)
# ---------------------------------------------------------------------------


def test_two_independent_registries_see_the_same_state_through_redis():
    """The literal Sprint 9H acceptance scenario: 'the worker' records
    a failure through one `RedisProviderHealthRegistry` instance;
    'the API' reads it through a completely separate instance with its
    own local fallback and its own Redis client. No object is shared
    between them — Redis is the only channel.
    """
    worker = RedisProviderHealthRegistry(redis_url=settings.celery_broker_url)
    api = RedisProviderHealthRegistry(redis_url=settings.celery_broker_url)
    worker.reset()

    try:
        # Before: neither process has an opinion beyond "configured".
        assert api.status(GEMINI).status in (
            ProviderStatus.CONFIGURED, ProviderStatus.NOT_CONFIGURED
        )

        # "The worker" observes a quota exhaustion during a research run.
        worker.record_failure(GEMINI, error=LLMQuotaExhaustedError("daily quota spent"))

        # "The API" — a different instance, different local fallback,
        # never called on directly — sees it through Redis alone.
        api_view = api.status(GEMINI)
        assert api_view.status is ProviderStatus.QUOTA_EXHAUSTED
        assert api_view.blocked is True
        assert api.is_blocked(GEMINI) is True

        # And the API instance's own *local* fallback registry — which
        # nothing ever wrote to — genuinely does not know this, proving
        # the information really did travel through Redis and not
        # through some other shared state.
        assert api._local.status(GEMINI).status is not ProviderStatus.QUOTA_EXHAUSTED
    finally:
        worker.reset()


def test_a_success_recorded_by_one_process_clears_it_for_the_other():
    """The recovery half of the same scenario."""
    worker = RedisProviderHealthRegistry(redis_url=settings.celery_broker_url)
    api = RedisProviderHealthRegistry(redis_url=settings.celery_broker_url)
    worker.reset()

    try:
        worker.record_failure(GROQ, error=LLMRateLimitError("429"))
        assert api.is_blocked(GROQ) is True

        worker.record_success(GROQ)

        assert api.status(GROQ).status is ProviderStatus.HEALTHY
        assert api.is_blocked(GROQ) is False
    finally:
        worker.reset()


# ---------------------------------------------------------------------------
# Phase 11 — Redis failure safety
# ---------------------------------------------------------------------------


def test_a_read_falls_back_to_local_when_redis_is_unreachable(broken_registry):
    """The gateway must keep functioning — reading degrades, not raises."""
    broken_registry._local.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))

    health = broken_registry.status(GEMINI)

    assert health.status is ProviderStatus.QUOTA_EXHAUSTED
    assert health.blocked is True


def test_a_write_never_raises_when_redis_is_unreachable(broken_registry):
    """Phase 11: a dead Redis must not become a new failure in the LLM path."""
    broken_registry.record_success(GEMINI)  # must not raise
    broken_registry.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))  # must not raise

    # And the local fallback still recorded it, so the gateway's own
    # process-local decision is still correct.
    assert broken_registry._local.status(GEMINI).status is ProviderStatus.QUOTA_EXHAUSTED


def test_reset_never_raises_when_redis_is_unreachable(broken_registry):
    broken_registry.reset()  # must not raise


def test_a_recovered_redis_is_used_again_without_restarting(redis_registry):
    """Degradation must not be permanent: once Redis answers again, the
    same instance goes back to treating it as authoritative."""
    real_client = redis_registry._client
    redis_registry._client = BrokenRedisClient()
    redis_registry.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))
    assert redis_registry._local.status(GEMINI).status is ProviderStatus.QUOTA_EXHAUSTED

    # Redis "comes back": swap the working client back in, exactly as
    # a reconnect would look from this class's point of view.
    redis_registry._client = real_client
    redis_registry.record_success(GROQ)

    client = redis_registry._get_client()
    assert client.get(f"{REDIS_KEY_PREFIX}{GROQ}") is not None


# ---------------------------------------------------------------------------
# Phase 13 — status semantics preserved
# ---------------------------------------------------------------------------


def test_configured_is_not_conflated_with_healthy_over_redis(redis_registry):
    """The Sprint 9G distinction must survive the move to Redis."""
    # Nothing recorded yet: the Redis-backed registry has no entry, so
    # it reports the credential-only baseline.
    health = redis_registry.status(GEMINI)
    assert health.status is not ProviderStatus.HEALTHY


def test_a_configuration_error_does_not_block_over_redis(redis_registry):
    redis_registry.record_failure(GEMINI, error=LLMAuthenticationError("401"))
    assert redis_registry.is_blocked(GEMINI) is False
    assert redis_registry.status(GEMINI).status is ProviderStatus.CONFIGURATION_ERROR


def test_keyed_by_model_not_provider_over_redis(redis_registry):
    """The Sprint 9G fix (a defect this deployment actually hit) must
    hold across the Redis boundary too."""
    redis_registry.record_failure(GEMINI, error=LLMQuotaExhaustedError("spent"))

    assert redis_registry.is_blocked(GEMINI) is True
    assert redis_registry.is_blocked("gemini/gemini-pro-latest") is False


# ---------------------------------------------------------------------------
# The gateway actually uses the Redis-backed registry by default
# ---------------------------------------------------------------------------


def test_get_provider_health_registry_returns_a_redis_backed_instance(monkeypatch):
    from app.core.llm import provider_health
    from app.core.llm.provider_health import get_provider_health_registry

    monkeypatch.setattr(provider_health, "_registry", None)
    registry = get_provider_health_registry()
    assert isinstance(registry, RedisProviderHealthRegistry)


# ---------------------------------------------------------------------------
# Sprint 13 Phase 3 — the full gateway call path, not just direct
# `record_failure()`, must also be visible across independent registries.
#
# Every test above writes through `record_failure()` directly. Nothing
# exercised the real path a research run actually takes: `LLMGateway.complete()`
# -> `_call_with_retries()` -> `record_failure()`, driven by a rejected
# `acompletion()` call rather than a hand-built error. A live discrepancy was
# observed during Sprint 13 Phase 2/3 diagnosis (a genuine quota-exhaustion
# recorded through that full path was not visible to a fresh process minutes
# later) that this repository's existing tests could not have caught, because
# none of them go through the gateway to produce the write. This closes that
# specific gap; it does not by itself explain the discrepancy.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failure_recorded_through_the_real_gateway_path_is_visible_to_a_fresh_registry(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.core.llm.gateway import LLMGateway

    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("test-gateway-key"))
    writer_health = RedisProviderHealthRegistry(redis_url=settings.celery_broker_url)
    writer_health.reset()
    gateway = LLMGateway(provider_health=writer_health)

    async def rejected(**kwargs):
        raise gemini_quota_error_for_this_file()

    try:
        with patch("app.core.llm.gateway.acompletion", new=AsyncMock(side_effect=rejected)):
            with pytest.raises(Exception):
                await gateway.generate(
                    prompt="hi", model=GEMINI, allow_fallback=False, max_retries=0
                )

        # A completely independent instance -- nothing shared but Redis --
        # must see the same state the gateway's own writer just produced.
        reader = RedisProviderHealthRegistry(redis_url=settings.celery_broker_url)
        reader_view = reader.status(GEMINI)
        assert reader_view.status is ProviderStatus.QUOTA_EXHAUSTED
        assert reader.is_blocked(GEMINI) is True
    finally:
        writer_health.reset()


def gemini_quota_error_for_this_file() -> Exception:
    """The same message shape LiteLLM raises for a spent Gemini daily
    quota (matching `test_llm_resilience.py`'s `gemini_daily_quota_error`
    convention: the gateway classifies by message content, not by
    exception type, so a plain `Exception` with the right text is
    enough), self-contained here rather than imported across test files."""
    return Exception(
        "litellm.RateLimitError: geminiException - {"
        '"error": {"code": 429, "message": "You exceeded your current quota, '
        'please check your plan and billing details.", "status": '
        '"RESOURCE_EXHAUSTED", "details": [{"@type": '
        '"type.googleapis.com/google.rpc.QuotaFailure", "violations": '
        '[{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}'
    )
