"""Application configuration loaded from environment variables.

Exposes a single cached `settings` instance backed by Pydantic Settings v2.
Only foundation-level configuration (app metadata, server, CORS, logging)
is defined here; settings for database, auth, and AI modules are added
alongside their respective milestones.
"""

import json
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


_QWEN_ESTIMATED_CHARACTERS_PER_TOKEN = 4
_QWEN_PROMPT_TOKEN_RESERVE = 512


class Settings(BaseSettings):
    """Strongly-typed application settings sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application metadata
    app_name: str = "AIKDAP"
    app_version: str = "1.0.0"
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # API
    api_v1_prefix: str = "/api/v1"

    # Security / JWT
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Database
    database_url: str
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_pre_ping: bool = True

    # CORS
    #
    # `NoDecode` opts this field out of pydantic-settings' automatic
    # JSON-decoding of complex-typed env values, which would otherwise
    # raise `SettingsError` on a plain comma-separated string before our
    # validator below ever runs. The validator then accepts either a
    # JSON array (`["https://a", "https://b"]`) or a comma-separated
    # string (`https://a,https://b`).
    backend_cors_origins: Annotated[list[str], NoDecode] = []

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Asset storage
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 100

    # Celery / background tasks
    celery_broker_url: str
    celery_result_backend: str

    # Document chunking (knowledge base pipeline)
    chunk_size: int = 1000
    chunk_overlap: int = 100

    # ------------------------------------------------------------------
    # LLM gateway (Sprint 9A)
    #
    # Model identifiers use LiteLLM's `provider/model` notation, which is
    # what routes a call to the right backend (`gemini/...`,
    # `ollama/...`, `groq/...`). Keeping them in configuration means
    # switching or upgrading a model is an environment change, not a
    # code change.
    #
    # `gemini_api_key` is a `SecretStr`: printing or logging the
    # settings object renders it as `**********`, so the key cannot leak
    # through a stray log line or a traceback. Read it explicitly with
    # `.get_secret_value()` at the single point of use.
    # ------------------------------------------------------------------
    gemini_api_key: SecretStr | None = None

    #: Credentials for the fallback providers, routed by `LLMGateway`
    #: since Sprint 9G. `SecretStr` for the same reason as
    #: `gemini_api_key`: printing settings renders them masked, and the
    #: value is read only at the single point of use.
    groq_api_key: SecretStr | None = None
    #: Kept configured but not part of the active chain (Sprint 9H):
    #: this deployment's DeepSeek account has zero balance
    #: ("Insufficient Balance", observed live 2026-08-16). The field
    #: and its credential plumbing are left in place rather than
    #: deleted — DeepSeek remains a usable provider if ever
    #: reconfigured and funded — but neither `FALLBACK_LLM` nor
    #: `SECONDARY_FALLBACK_LLM` names it by default any more.
    deepseek_api_key: SecretStr | None = None
    #: OpenRouter (Sprint 9H): the third fallback, replacing DeepSeek.
    openrouter_api_key: SecretStr | None = None
    #: Jina AI: embeddings (`jina_ai/jina-embeddings-v3`) and reranking,
    #: for deployments with no local model server. Not part of the
    #: generation fallback chain — Jina serves neither completions nor
    #: any model this codebase generates text with.
    jina_api_key: SecretStr | None = None
    #: Tavily web search: the research workflow's external source, used
    #: only as a fallback when the project's own evidence is insufficient.
    #: Unset keeps external research on the clearly-labelled simulated
    #: provider, so no deployment starts sending queries to a third party
    #: without an explicit key.
    tavily_api_key: SecretStr | None = None
    tavily_timeout: float = Field(default=20.0, gt=0)

    @field_validator("tavily_api_key", mode="before")
    @classmethod
    def blank_tavily_key_is_unset(cls, value: object) -> object:
        """A bare `TAVILY_API_KEY=` in .env means "not configured".

        Without this it parses as `SecretStr("")` rather than `None`
        (the same trap `HttpRerankerProvider._headers` guards against),
        and an empty key would switch on a live provider that can only
        ever fail authentication.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    #: OpenAlex Works API. Unconfigured means paper suggestions are
    #: skipped entirely (see `agents.planner.graph`'s `paper_suggestion`
    #: routing) rather than answered with simulated results.
    openalex_api_key: SecretStr | None = None
    openalex_timeout: float = Field(default=20.0, gt=0)

    #: Milestone 10 step 3 (Add & re-run): connect/read timeout for
    #: downloading one OpenAlex open-access PDF. Same style/naming as
    #: `openalex_timeout` above.
    paper_import_download_timeout: float = Field(default=30.0, gt=0)
    #: Maximum redirect hops `paper_import.download_oa_pdf` will follow;
    #: every hop is re-validated for scheme and host (SSRF guard), so
    #: this bounds worst-case redirect-chasing rather than trusting
    #: httpx's own follower.
    paper_import_max_redirects: int = Field(default=3, ge=0)

    @field_validator("openalex_api_key", mode="before")
    @classmethod
    def blank_openalex_key_is_unset(cls, value: object) -> object:
        """A bare `OPENALEX_API_KEY=` in .env means "not configured".

        Same rationale as `blank_tavily_key_is_unset`: without this it
        parses as `SecretStr("")` rather than `None`, and an empty key
        would switch on a "configured" provider that can only ever fail.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    #: Model used when a caller does not name one — the primary in the
    #: fallback chain.
    default_llm: str = "gemini/gemini-pro-latest"
    #: Tried when the primary fails with something a different provider
    #: could plausibly survive. Empty disables fallback.
    fallback_llm: str | None = None
    #: Tried when the first fallback also fails (Sprint 9G). Empty
    #: disables it. A provider whose API key is missing is skipped
    #: cleanly rather than attempted and failed.
    #:
    #: The model lives inside this value (`openrouter/<vendor id>`)
    #: rather than in a separate `OPENROUTER_MODEL` setting, the same
    #: way Groq's fallback model lives inside `FALLBACK_LLM` rather
    #: than a `GROQ_MODEL` field — one source of truth per provider
    #: slot, consistent with the convention Sprint 9G already
    #: established.
    secondary_fallback_llm: str | None = None

    #: Per-role models. `synthesis_model` is what the research
    #: workflow's grounded synthesis step asks the gateway for.
    planner_model: str = "gemini/gemini-pro-latest"
    synthesis_model: str = "gemini/gemini-pro-latest"

    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=2048, gt=0)
    llm_timeout: float = Field(default=60.0, gt=0)

    # ------------------------------------------------------------------
    # Provider resilience (Sprint 9G)
    #
    # One retry policy, applied in the gateway and nowhere else. Nodes,
    # services and routers must not add their own — layered retries
    # multiply, so three levels of "just three attempts" is 27 calls
    # against a provider that already said no.
    #
    # `llm_max_retries` counts RETRIES, not attempts: 2 means up to 3
    # calls. Only transient failures are retried (per-minute rate
    # limits, 503s, timeouts, connection errors); a spent daily quota
    # never is, however many retries are configured.
    #
    # Delays are exponential with equal jitter, capped by
    # `llm_retry_max_delay`: roughly 0.5-1s, then 1-2s, then 2-4s at
    # the defaults. Equal rather than full jitter so a retry cannot
    # collapse to an immediate repeat, which would defeat the point.
    # ------------------------------------------------------------------
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_retry_base_delay: float = Field(default=1.0, ge=0.0)
    llm_retry_max_delay: float = Field(default=8.0, ge=0.0)

    # How long a recorded provider failure keeps the gateway from
    # retrying that provider. Every one is bounded so the breaker
    # self-heals without operator action.
    #
    # The quota cooldown is the load-bearing one. Gemini's free tier
    # resets at midnight Pacific, which this process cannot compute
    # reliably, so instead of guessing the wall-clock boundary the
    # entry simply ages out after an hour: at worst one wasted request
    # per hour re-confirms the quota is still spent. The alternative —
    # no cooldown — would disable the provider until a restart.
    llm_quota_cooldown_seconds: float = Field(default=3600.0, gt=0)
    llm_rate_limit_cooldown_seconds: float = Field(default=60.0, gt=0)
    llm_unavailable_cooldown_seconds: float = Field(default=60.0, gt=0)
    llm_configuration_error_cooldown_seconds: float = Field(default=300.0, gt=0)

    # ------------------------------------------------------------------
    # Redis-backed provider health (Sprint 9H)
    #
    # Sprint 9G's breaker lived in one process's memory, so a quota
    # exhaustion the worker discovered mid-research-run was invisible to
    # the API's `/health` until the API made its own failing call.
    # Backing it with Redis — already-required infrastructure, the same
    # broker Celery uses — makes the state visible to both processes
    # immediately, at the cost of one small Redis round trip per gateway
    # attempt.
    #
    # `llm_health_redis_timeout` is deliberately tiny. The breaker is a
    # synchronous client call inside an async method (see
    # `provider_health.RedisProviderHealthRegistry`), so a slow or dead
    # Redis must fail fast rather than stall the event loop — a bounded
    # 200ms of blocking is an acceptable trade against the alternative
    # (a full async client + connection-pool lifecycle for a value this
    # small), and it is far shorter than any LLM call it sits beside.
    #
    # `llm_health_state_ttl_seconds` is the hygiene TTL for states that
    # have no cooldown of their own (`healthy`, `configured`) — so a
    # Redis key is never permanent even for a provider nobody has
    # touched in days. It does not gate retry behaviour; only the
    # per-status cooldowns above do that.
    # ------------------------------------------------------------------
    llm_health_redis_timeout: float = Field(default=0.2, gt=0)
    llm_health_state_ttl_seconds: float = Field(default=86_400.0, gt=0)

    # ------------------------------------------------------------------
    # Stale research-run reconciliation (Sprint 9J)
    #
    # `ResearchExecutionService.execute()` sets `status=RUNNING` before
    # doing any real work and only reaches `_complete()`/`_fail()` if
    # its own Python process survives to run them. A worker crash, OOM
    # kill, or container restart mid-run skips both — the row is left
    # at `RUNNING` forever, with nothing in the architecture that would
    # ever revisit it (confirmed: a stale run from an earlier sprint's
    # interrupted manual test is still sitting at `RUNNING` days later).
    #
    # `research_run_stale_after_seconds` is the conservative ceiling
    # below which a `RUNNING` run must be left alone. Sized from the
    # worst case the current retry/timeout configuration can produce in
    # one execution attempt, not from typical observed durations (which
    # are seconds, not minutes): up to 3 providers in the fallback
    # chain, each retried up to `llm_max_retries` times at up to
    # `llm_timeout` seconds per attempt, plus backoff between retries,
    # is roughly 3 x 3 x 60s, ~9 minutes, in the pathological case where
    # every single call on every provider times out fully before
    # falling through — retrieval and reranking add at most a few more
    # seconds on top of that. 20 minutes leaves a wide safety margin
    # above that ceiling while still being short enough that a
    # genuinely stuck run does not sit unreconciled for days. It does
    # not gate retry behaviour — only reconciliation reads it.
    # ------------------------------------------------------------------
    research_run_stale_after_seconds: float = Field(default=1_200.0, gt=0)

    # ------------------------------------------------------------------
    # Stale execution-attempt reconciliation (Sprint 16 Phase 7B.15)
    #
    # `prepare_approved_launch()` commits an `ExecutionAttempt` at
    # `PENDING_CREATE` *before* any Docker call, deliberately, so a
    # crashed launcher leaves durable evidence rather than an orphan
    # container nothing knows about (Phase 7B.12 recovery protocol). The
    # cost of that ordering is that a launcher which dies between the
    # commit and the Docker call leaves the row at `PENDING_CREATE`
    # forever — the same "nothing ever revisits this row" gap Sprint 9J
    # found for `ResearchRun`.
    #
    # UNVERIFIED number. Unlike `research_run_stale_after_seconds` — sized
    # from the real retry/timeout configuration it has to clear — there is
    # no Docker call in this system yet, so there is nothing to measure a
    # normal `PENDING_CREATE` dwell time against. 300s is chosen as a
    # conservative ceiling over the slowest plausible container creation
    # (a cold image pull), not as a benchmarked value, and must be
    # re-derived once a real launcher exists. It gates reconciliation
    # only; nothing else reads it.
    # ------------------------------------------------------------------
    execution_attempt_stale_after_seconds: float = Field(default=300.0, gt=0)

    # ------------------------------------------------------------------
    # Stale execution-job VALIDATING reconciliation (Sprint 16 Phase 7B.16)
    #
    # `ExecutionJobRepository.claim_pending_job()` moves a job
    # `PENDING -> VALIDATING` and commits immediately. Phase 7B.10's
    # documented failure policy then deliberately leaves the job parked
    # at `VALIDATING` forever on a guard/resolver rejection, an
    # attempt-creation failure, or a crash anywhere in between -- the
    # same "nothing ever revisits this row" gap Sprint 9J found for
    # `ResearchRun` and Phase 7B.15 found for `ExecutionAttempt`.
    #
    # Deliberately a SEPARATE setting from
    # `execution_attempt_stale_after_seconds`, not a reuse of it: the two
    # cover structurally different spans. The attempt threshold's 300s
    # accounts for a cold Docker image pull that has not happened yet.
    # `VALIDATING` covers only `reconstruct_launch_request()` ->
    # `ProductionInputResolver` -> `build_candidate()` ->
    # `validate_and_approve()` -> attempt creation -- pure in-process
    # Python plus Postgres/local-filesystem I/O, no Docker call anywhere
    # in that span. UNVERIFIED number: not benchmarked against real
    # production timings (none exist yet), only reasoned from the
    # structural absence of any Docker wait in this stage; a materially
    # smaller ceiling than the attempt threshold is justified by that
    # absence, not by measurement, and must be re-derived once real
    # timing data exists. It gates reconciliation only.
    # ------------------------------------------------------------------
    execution_job_validating_stale_after_seconds: float = Field(default=120.0, gt=0)

    # ------------------------------------------------------------------
    # Stale report-generation reconciliation (Milestone 10 step 4)
    #
    # `workers.tasks._generate_report` sets `processing_status=RUNNING`
    # before invoking the report LangGraph, and only reaches its own
    # `except`/success path if the worker process survives long enough
    # to run it. A worker crash mid-generation leaves the asset at
    # `running` (or, if the worker dies before the task even starts,
    # `pending`) forever — the same "nothing ever revisits this row" gap
    # Sprint 9J found for `ResearchRun`, reused here rather than
    # reinvented. Sized the same way: one report section calls the LLM
    # gateway at most once, so the worst case is a single
    # `llm_max_retries`-bounded call chain across the fallback providers,
    # well inside `research_run_stale_after_seconds`'s own 20-minute
    # ceiling — reused directly rather than duplicating the same
    # reasoning under a new number.
    # ------------------------------------------------------------------
    report_generation_stale_after_seconds: float = Field(default=1_200.0, gt=0)

    #: Whether research synthesis answers with a real model
    #: (`GroundedSynthesizer`) or extracts from the evidence without one
    #: (`ExtractiveSynthesizer`). There is deliberately no automatic
    #: downgrade between them: if this is on and the model cannot be
    #: reached, the run fails with that reason rather than quietly
    #: answering by a different mechanism.
    synthesis_grounded: bool = True
    #: Character budget for the evidence placed in the synthesis prompt.
    #: Evidence beyond it is excluded — and therefore cannot be cited,
    #: since the model never sees it. Sized well below the model's
    #: context window so the system prompt and question always fit.
    synthesis_max_evidence_characters: int = Field(default=12_000, gt=0)

    @property
    def has_gemini_credentials(self) -> bool:
        """Whether a non-empty Gemini API key is configured.

        Lets callers check for credentials without ever reading the
        secret itself.
        """
        return bool(self.gemini_api_key and self.gemini_api_key.get_secret_value().strip())

    @property
    def has_groq_credentials(self) -> bool:
        """Whether a non-empty Groq API key is configured."""
        return bool(self.groq_api_key and self.groq_api_key.get_secret_value().strip())

    @property
    def has_deepseek_credentials(self) -> bool:
        """Whether a non-empty DeepSeek API key is configured."""
        return bool(self.deepseek_api_key and self.deepseek_api_key.get_secret_value().strip())

    @property
    def has_openrouter_credentials(self) -> bool:
        """Whether a non-empty OpenRouter API key is configured."""
        return bool(
            self.openrouter_api_key and self.openrouter_api_key.get_secret_value().strip()
        )

    @property
    def has_jina_credentials(self) -> bool:
        """Whether a non-empty Jina AI API key is configured."""
        return bool(self.jina_api_key and self.jina_api_key.get_secret_value().strip())

    # ------------------------------------------------------------------
    # Local document intelligence — Ollama + Qwen (Sprint 9B)
    #
    # `ollama_base_url` defaults to `host.docker.internal`, which is
    # what actually resolves from inside the backend/worker containers
    # on this Docker Desktop (Windows) setup — verified live, not
    # assumed, before picking this default. Compose pins this hostname
    # to `host-gateway` for both backend and worker so it cannot drift
    # onto the machine's roaming LAN address.
    #
    # `qwen_model` is the literal Ollama model tag — verified present
    # via `ollama list` (`qwen3.5:4b`, 3.4 GB). No other model is ever
    # silently substituted; an unavailable model is a reported error
    # (see `app.core.llm.gateway.LLMModelNotFoundError`), not a
    # fallback trigger.
    #
    # `qwen_think` defaults to disabled: Qwen 3.5 is a hybrid
    # "thinking" model, and live testing against this host showed it
    # can spend its entire completion budget on internal reasoning
    # before emitting any visible text (431 completion tokens for a
    # one-word reply, 0 of them the reply itself). Disabling thinking
    # dropped that same call to 2 tokens / ~10s and produced accurate,
    # correctly-grounded structured output for the real test document
    # in ~25s. Configurable, not hardcoded, in case a future document
    # genuinely benefits from the deeper (slower) reasoning mode.
    #
    # `qwen_timeout` is deliberately much larger than `llm_timeout`
    # (tuned for cloud latency): CPU-only local inference of a 4B model
    # is far slower than a cloud API call, especially on a cold model
    # load (~40s observed) plus generation.
    # ------------------------------------------------------------------
    ollama_base_url: str = "http://host.docker.internal:11434"
    qwen_model: str = "qwen3.5:4b"
    qwen_think: bool = False
    #: Raised after a live response hit the old 1024-token limit in the
    #: middle of a JSON string (`Unterminated string` at char 2436).
    qwen_max_tokens: int = Field(default=4096, gt=0)
    qwen_timeout: float = Field(default=180.0, gt=0)

    #: Ollama's context window for Qwen document-understanding calls.
    #: The live `qwen3.5:4b` model reports a 262144-token context; 16384
    #: is deliberately smaller to bound local memory while comfortably
    #: fitting this service's request. Budgeting assumes typical English
    #: paper text averages 4 characters per token, plus 512 tokens for the
    #: fixed system/user prompt. The validator below also reserves the full
    #: `qwen_max_tokens` output so Ollama cannot silently truncate input.
    qwen_num_ctx: int = Field(default=16_384, gt=0)

    #: Timeout for `/health`'s Ollama liveness probe (Sprint 11.2) — a
    #: plain GET on the root route, not a model call. Short on purpose,
    #: matching `reranker_health_timeout`: a health check must be
    #: cheaper than the thing it reports on.
    ollama_health_timeout: float = Field(default=3.0, gt=0)

    #: Character budget for a single Qwen call. Longer extracted text is
    #: split with the existing `chunk_text()` utility and processed as
    #: multiple calls, then merged deterministically — see
    #: `app.modules.assets.processing.document_understanding`.
    qwen_max_input_characters: int = Field(default=24_000, gt=0)

    #: Sprint 12.3: fraction of a PDF's pages that must be empty of
    #: machine-readable text before the WHOLE document is treated as
    #: `OCR_REQUIRED` rather than `COMPLETED`. Before this existed, only
    #: a document with EVERY page empty triggered OCR_REQUIRED -- a
    #: real-world PDF with a handful of pages of genuine text (a cover
    #: page, a stamped page number) followed by dozens of scanned image
    #: pages was marked COMPLETED with just that small fraction indexed,
    #: silently dropping the rest of the document while looking
    #: successful. Confirmed live: a synthetic 5-page PDF (1 real text
    #: page + 4 image-only pages) returned COMPLETED with 1 chunk
    #: before this fix. 0.5 (a majority) is deliberately conservative --
    #: it only fires when most of the document is unreadable, so a
    #: document with a few genuinely blank pages (a section divider, a
    #: back cover) is unaffected.
    pdf_ocr_required_empty_page_ratio: float = Field(default=0.5, gt=0.0, le=1.0)

    # ------------------------------------------------------------------
    # OCR (Sprint 12.5) -- Tesseract, invoked via `pytesseract`.
    #
    # `ocr_enabled` is a deployment-wide kill switch: a deployment that
    # never installed the `tesseract-ocr` system package (see
    # Dockerfile) can set this false to fail fast and honestly
    # (`OCR_REQUIRED`, unchanged from Sprint 12.3) instead of hitting a
    # `TesseractNotFoundError` on every scanned-page attempt.
    #
    # `ocr_min_confidence`/`ocr_min_characters` are the quality gate,
    # calibrated live against this deployment's own Tesseract 5.5.0:
    # genuinely readable rendered text (clean, mildly blurred, and a
    # tiny 8pt font) scored 79-96 average confidence; random noise, a
    # blank image, heavily blurred text, and 15-degree-rotated text all
    # scored exactly 0 (zero words recognized at all). The threshold
    # sits well inside that gap on the conservative side -- rejecting
    # only when OCR found nothing worth trusting, not merely imperfect
    # text.
    # ------------------------------------------------------------------
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    ocr_min_confidence: float = Field(default=40.0, ge=0.0, le=100.0)
    ocr_min_characters: int = Field(default=10, gt=0)
    #: How long Tesseract may spend on one image before being killed --
    #: bounds worst-case per-page latency (Sprint 12.5 Phase 10).
    ocr_timeout_seconds: float = Field(default=30.0, gt=0)
    #: Hard ceiling on pages OCR'd per PDF. Only pages with no native
    #: text are ever candidates, so this bounds the pathological case
    #: (a many-hundred-page scanned PDF) rather than normal documents,
    #: which stay far under it.
    ocr_max_pages_per_document: int = Field(default=50, gt=0)
    #: Per-image pixel-count ceiling, checked before Tesseract ever
    #: runs. Set well below Pillow's own ~89-megapixel
    #: `DecompressionBombWarning` threshold so an oversized embedded
    #: image is skipped cheaply rather than decoded first and warned
    #: about after the fact. 40 megapixels comfortably covers even a
    #: 600 DPI scan of a Letter page (~34 megapixels).
    ocr_max_image_pixels: int = Field(default=40_000_000, gt=0)
    #: Sprint 12.5 Phase 3: minimum Tesseract OSD `orientation_conf`
    #: required to trust its rotation suggestion. Calibrated live: real
    #: rotated text (0/90/180/270 degrees) scored 3.7-3.9; pure random
    #: noise scored 0.13 despite OSD returning a "successful" (non-
    #: exception) result. The threshold sits well below the real-text
    #: range and well above the noise measurement, so a genuinely
    #: unreadable/blank image (which usually raises its own exception,
    #: handled separately) or a noise-like one is left unrotated rather
    #: than confidently "corrected" based on a meaningless suggestion.
    ocr_osd_min_confidence: float = Field(default=1.0, ge=0.0)

    #: Sprint 12.2: hard ceiling on how many sections `analyze()` will
    #: send to Qwen for one document. Without this, a very large upload
    #: (a several-hundred-page PDF) had no upper bound on sequential
    #: Qwen calls — document understanding produces a summary, not an
    #: exhaustive extraction, so processing the leading N sections
    #: (which carry the title, abstract/intro, and early structure of
    #: most real documents) is a reasonable trade for a bounded worst
    #: case. The full text remains fully searchable regardless of this
    #: cap: retrieval chunking (`chunk_document`) is a separate,
    #: uncapped path over the complete extracted text.
    qwen_max_sections: int = Field(default=20, gt=0)

    @model_validator(mode="after")
    def validate_qwen_context_budget(self) -> Self:
        """Keep Qwen input, prompt, and output within Ollama's context."""
        estimated_input_tokens = (
            self.qwen_max_input_characters + _QWEN_ESTIMATED_CHARACTERS_PER_TOKEN - 1
        ) // _QWEN_ESTIMATED_CHARACTERS_PER_TOKEN
        required_context = (
            estimated_input_tokens
            + _QWEN_PROMPT_TOKEN_RESERVE
            + self.qwen_max_tokens
        )
        if self.qwen_num_ctx < required_context:
            raise ValueError(
                "qwen_num_ctx is too small for qwen_max_input_characters, "
                "the prompt reserve, and qwen_max_tokens: "
                f"requires at least {required_context} tokens"
            )
        return self

    # ------------------------------------------------------------------
    # Embeddings — pgvector, model selected by provider prefix (Sprint 9C)
    #
    # `embedding_model` reuses the `EMBEDDING_MODEL` env var that
    # already existed in `.env` (previously an orphaned setting: no
    # `Settings` field read it, and it named a cloud OpenAI model
    # `text-embedding-3-small` that nothing in this codebase calls).
    # Repointed at the model this codebase actually implements rather
    # than introducing a second config key for the same concept.
    #
    # The value carries a LiteLLM provider prefix, exactly like
    # `default_llm` and every other model setting: `ollama/bge-m3` for a
    # host running a local model server, `jina_ai/jina-embeddings-v3`
    # for a deployment (Render, Fly, ...) that has none. The prefix is
    # the only thing that selects the backend — see
    # `knowledge_base.embeddings.GatewayEmbeddingProvider`. Both of
    # those models emit 1024 floats, so they share this column width;
    # any model that does not requires an Alembic migration first.
    #
    # `embedding_dimension` is not a guess: verified live against this
    # host's Ollama instance (`litellm.aembedding(model="ollama/bge-m3",
    # ...)`) before being set — BGE-M3 returned exactly 1024 floats.
    # Declared as configuration (not hardcoded in the pgvector column
    # definition) because the column type must match whatever this
    # value is, and Alembic reads it at migration time.
    # ------------------------------------------------------------------
    embedding_model: str = "ollama/bge-m3"
    embedding_dimension: int = Field(default=1024, gt=0)
    # ------------------------------------------------------------------
    # Two-stage retrieval reranking — BGE-Reranker-v2-m3 (Sprint 9D)
    #
    # RUNTIME: llama.cpp (`llama-server --reranking`), not Ollama.
    # Ollama cannot serve this model — verified live on this host: no
    # rerank endpoint exists at all (`/api/rerank`, `/v1/rerank`,
    # `/rerank`, `/api/rank`, `/v1/reranking` -> 404), it advertises the
    # model's capability as `completion` only, and both `/api/embed` and
    # `/api/generate` crash the llama-server subprocess (exit
    # 0xc0000409). BGE-M3 and Qwen worked at the same moment, so the
    # incompatibility is specific to the cross-encoder.
    #
    # Because `reranker_base_url`/`reranker_endpoint_path` were
    # configuration rather than hardcoded, moving to a runtime that can
    # serve the model needed no code change: llama.cpp already speaks
    # the standard rerank contract this provider implements
    # (`{model, query, documents}` -> `{results: [{index,
    # relevance_score}]}`). The model itself is unchanged — the same
    # GGUF, BAAI/bge-reranker-v2-m3 Q8_0. No other model is ever
    # substituted; if the endpoint is unreachable the search degrades to
    # retrieval-only and says so via `reranking_status`, never silently.
    #
    # `reranker_candidate_k` > `reranker_top_k` on purpose: stage 1
    # over-retrieves so stage 2 has something to reorder. Retrieving
    # only `top_k` before reranking would make the second stage
    # pointless.
    # ------------------------------------------------------------------
    reranker_enabled: bool = True
    reranker_model: str = "bge-reranker-v2-m3"
    #: Where llama-server is listening. Empty falls back to
    #: `ollama_base_url`, which cannot serve this model and will report
    #: `reranking_status=unavailable` — a safe, loud default rather than
    #: a working one, so a deployment that forgets to set this degrades
    #: visibly instead of appearing to rerank.
    reranker_base_url: str = ""
    #: llama.cpp's reranking route. It also answers on `/v1/rerank` and
    #: `/rerank`; this one is the documented native path.
    reranker_endpoint_path: str = "/reranking"
    #: llama-server's liveness route (Sprint 9G). `GET`ting it is free —
    #: it loads no context and scores nothing — which is exactly why
    #: `/health` probes this instead of sending a real rerank request.
    #:
    #: Empty disables the probe, for a hosted reranker that publishes no
    #: liveness route (Jina AI, Cohere). Probing a path such a provider
    #: does not serve would report a permanently unhealthy reranker that
    #: reranks perfectly well, so absence of a probe is reported as
    #: "not probed", never as failure — see `check_reranker_health`.
    reranker_health_path: str = "/health"
    #: Bearer token for a hosted reranker. Empty for a local llama-server,
    #: which needs no auth. Kept vendor-neutral (not a Jina-specific
    #: field) for the same reason `reranker_base_url` is: this provider
    #: speaks the standard rerank contract, so swapping the endpoint
    #: behind it stays configuration rather than code.
    reranker_api_key: SecretStr | None = None
    #: Health probes must not inherit the 60s inference timeout: an
    #: unreachable reranker would then stall the health endpoint for a
    #: minute, turning a diagnostic into an outage of its own.
    reranker_health_timeout: float = Field(default=3.0, gt=0)
    reranker_timeout: float = Field(default=60.0, gt=0)
    reranker_candidate_k: int = Field(default=20, gt=0)
    reranker_top_k: int = Field(default=5, gt=0)
    #: Cross-encoders have a fixed context window (8192 tokens for this
    #: model). Candidates are truncated to this many characters before
    #: scoring so one oversized chunk cannot fail the whole batch.
    reranker_max_document_characters: int = Field(default=4000, gt=0)

    #: Minimum `rerank_score` for a chunk to be used as evidence
    #: (Sprint 9F). A reranker asked for the top 5 always returns 5,
    #: however irrelevant, so ordering alone cannot keep an unrelated
    #: question from reaching the synthesis model with confident-looking
    #: evidence attached.
    #:
    #: This is a raw BGE-Reranker-v2-m3 logit, not a probability, and is
    #: NOT comparable to a similarity score. It was measured, not
    #: chosen: see `docs/relevance-calibration.md` for the 70-pair
    #: fixture behind it (precision 0.818, recall 0.818 at this value).
    #: There is no meaningful default for a different corpus or a
    #: different reranker — recalibrate rather than reusing this number.
    #: Lower admits more evidence; raise it to filter harder.
    reranker_relevance_threshold: float = -2.0

    #: Sprint 15: Validated Query Understanding
    #: Feature flag to enable/disable safe LLM query reformulation before retrieval.
    query_reformulation_enabled: bool = True

    @property
    def resolved_reranker_base_url(self) -> str:
        """The reranker endpoint's base URL.

        Falls back to Ollama's only so an unconfigured deployment fails
        loudly (404 -> `reranking_status=unavailable`) instead of
        silently pointing at nothing.
        """
        return (self.reranker_base_url or self.ollama_base_url).rstrip("/")
    #: Embeddings are a single forward pass with no "thinking" overhead
    #: (unlike Qwen's chat completions), so this is far smaller than
    #: `qwen_timeout`.
    embedding_timeout: float = Field(default=60.0, gt=0)

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, value: str | list[str]) -> list[str]:
        """Parse CORS origins from a JSON array or a comma-separated string."""
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return []

        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"backend_cors_origins is not valid JSON: {stripped!r}"
                ) from exc
            if not isinstance(parsed, list):
                raise ValueError("backend_cors_origins JSON value must be an array")
            return [str(origin).strip() for origin in parsed if str(origin).strip()]

        return [origin.strip() for origin in stripped.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        """Whether the app is running in the production environment."""
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton.

    Using `lru_cache` ensures the environment is parsed once and reused,
    while still allowing tests to override via `get_settings.cache_clear()`.
    """
    return Settings()


settings: Settings = get_settings()
