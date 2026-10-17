"""Embedding provider abstraction.

Defines `EmbeddingProvider`, the interface every embedding backend
(OpenAI, NVIDIA NIM, Voyage AI, Jina AI, a local model) implements.

`GatewayEmbeddingProvider` is the real implementation, using the same
centralized `LLMGateway` (Sprint 9A) that document understanding
(Sprint 9B) already drives models through — no second HTTP client. It
is provider-agnostic: the backend is chosen by `EMBEDDING_MODEL`'s
LiteLLM provider prefix (`ollama/bge-m3` for the local BGE-M3 that
Sprint 9C shipped, `jina_ai/jina-embeddings-v3` for a hosted
deployment where no local model server exists), so no subclass or
branch is needed per provider. `NullEmbeddingProvider` remains as the
structural placeholder Sprint 6 shipped, kept for tests and for any
deployment that runs with no embedding backend configured.

Changing provider changes the vector space: stored embeddings from a
previous model stay numerically valid but are no longer comparable, so
a provider switch requires re-embedding before search is meaningful.
"""

from abc import ABC, abstractmethod
from functools import lru_cache

from app.core.config import settings
from app.core.llm import LLMGateway, get_llm_gateway, provider_of
from app.modules.knowledge_base.enums import EmbeddingProviderName
from app.modules.knowledge_base.spaces import EmbeddingSpace, validate_vectors


class EmbeddingProvider(ABC):
    """Abstract interface for turning text into embedding vectors."""

    @property
    def space(self) -> EmbeddingSpace:
        # Explicit identity for non-gateway adapters/test doubles. Never labels legacy data.
        return EmbeddingSpace(self.name.value, type(self).__qualname__, "1", self.dimensions)

    async def embed_query(self, texts: list[str]) -> list[list[float]]:
        return await self.embed(texts)

    @property
    @abstractmethod
    def name(self) -> EmbeddingProviderName:
        """Identifier for this provider, stored on each embedded chunk."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector dimensionality this provider produces."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, returning one vector per input, in order."""


class NullEmbeddingProvider(EmbeddingProvider):
    """Placeholder provider: performs no real embedding.

    Exists so the pipeline's shape (extract -> chunk -> embed) and the
    `EmbeddingProvider` interface are real and testable today, even
    though no backend is wired in yet.
    """

    @property
    def name(self) -> EmbeddingProviderName:
        return EmbeddingProviderName.NONE

    @property
    def dimensions(self) -> int:
        return 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(
            "No embedding provider is configured. NullEmbeddingProvider is "
            "a structural placeholder; real embedding generation (OpenAI, "
            "NVIDIA NIM, Voyage AI, Jina AI, or a local model) is future work."
        )


#: Maps a LiteLLM provider prefix to the provenance label persisted on
#: every chunk. A model whose provider is absent here is rejected at
#: construction rather than defaulted: the label records *which* vector
#: space a stored embedding belongs to, so guessing it would silently
#: corrupt the one field that makes a re-embed auditable.
_PROVIDER_LABELS: dict[str, EmbeddingProviderName] = {
    "ollama": EmbeddingProviderName.LOCAL,
    "jina_ai": EmbeddingProviderName.JINA_AI,
    "openai": EmbeddingProviderName.OPENAI,
    "voyage": EmbeddingProviderName.VOYAGE_AI,
    "nvidia_nim": EmbeddingProviderName.NVIDIA_NIM,
}


class GatewayEmbeddingProvider(EmbeddingProvider):
    """Real embedding provider: any LiteLLM-supported model, via `LLMGateway`.

    `settings.embedding_model` carries its provider prefix
    (`ollama/bge-m3`, `jina_ai/jina-embeddings-v3`) exactly as
    `DEFAULT_LLM` and every other model setting already does, and
    `LLMGateway.embed` is provider-generic — it injects `api_base` only
    for Ollama. Moving between a local and a hosted embedding backend
    is therefore a configuration change, not a code change.

    Switching provider does *not* migrate existing vectors: a different
    model is a different vector space, so previously embedded chunks
    must be re-embedded before similarity search is meaningful again.

    `dimensions` is fixed to `settings.embedding_dimension` (1024,
    verified live against this deployment's model before being set as
    the default — see `settings.py`) rather than re-derived per call:
    the pgvector column width is committed at migration time, so a
    provider whose model started returning a different width would
    need a schema change anyway, not a runtime adaptation.
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or get_llm_gateway()
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimension
        self._space = EmbeddingSpace(
            provider_of(self._model), self._model, settings.embedding_model_version,
            self._dimensions, settings.embedding_document_task, settings.embedding_query_task,
        )
        provider = provider_of(self._model)
        if provider not in _PROVIDER_LABELS:
            raise ValueError(
                f"EMBEDDING_MODEL={self._model!r} has no recognized provider "
                f"prefix. Expected one of {sorted(_PROVIDER_LABELS)} as in "
                "'jina_ai/jina-embeddings-v3' or 'ollama/bge-m3'."
            )
        self._name = _PROVIDER_LABELS[provider]

    @property
    def name(self) -> EmbeddingProviderName:
        return self._name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    async def embed_query(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, self.space.query_task)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. `LLMError` propagates unchanged so the
        caller can distinguish an unreachable embedding backend from a
        genuinely bad response — mirroring
        `QwenDocumentUnderstandingService`'s failure contract."""
        return await self._embed(texts, self.space.document_task)

    async def _embed(self, texts: list[str], task: str | None) -> list[list[float]]:
        options = {"task": task} if task is not None else {}
        response = await self._gateway.embed(
            texts=texts,
            model=self._model,
            timeout=settings.embedding_timeout,
            **options,
        )
        if response.dimension != self.dimensions:
            raise ValueError(
                f"{self._model} returned {response.dimension}-dimensional "
                f"vectors; the configured pgvector column is "
                f"{self.dimensions}-dimensional (EMBEDDING_DIMENSION). Update "
                "the configuration and re-run the migration rather than "
                "storing a mismatched vector."
            )
        validate_vectors(response.vectors, len(texts), self.dimensions)
        return response.vectors


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Dependency provider for the configured embedding backend.

    Returns `GatewayEmbeddingProvider`, which resolves its backend from
    `EMBEDDING_MODEL`'s provider prefix — so selecting a local
    (`ollama/bge-m3`) or hosted (`jina_ai/jina-embeddings-v3`) model is
    configuration, with no branch to add here per provider.
    """
    return GatewayEmbeddingProvider()
