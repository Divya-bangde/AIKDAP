"""Embedding compatibility validation independent of PostgreSQL or providers."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core.config import Settings, settings
from app.modules.knowledge_base.embeddings import GatewayEmbeddingProvider
from app.modules.knowledge_base.spaces import EmbeddingSpace, validate_vectors


def test_equal_dimensions_do_not_make_bge_and_jina_compatible():
    bge = EmbeddingSpace("ollama", "ollama/bge-m3", "v1", 1024)
    jina = EmbeddingSpace("jina_ai", "jina_ai/jina-embeddings-v3", "v1", 1024)
    assert bge.dimensions == jina.dimensions
    assert bge.id != jina.id
    for change in ({"version": "2"}, {"query_task": "classification"},
                   {"document_task": "other"}, {"dimensions": 512},
                   {"provider": "other"}, {"model": "other"}):
        assert replace(bge, **change).id != bge.id


def test_blank_example_task_settings_do_not_send_empty_task_to_provider():
    configured = Settings.model_validate({**settings.model_dump(),
        "embedding_document_task": "", "embedding_query_task": "  "})
    assert configured.embedding_document_task is None and configured.embedding_query_task is None


@pytest.mark.parametrize("vectors,count,dimension", [([], 1, 1024), ([[0.0]], 1, 1),
    ([[float("nan")]], 1, 1), ([[float("inf")]], 1, 1), ([[1.0]], 1, 1024)])
def test_invalid_vectors_are_rejected(vectors, count, dimension):
    with pytest.raises(ValueError):
        validate_vectors(vectors, count, dimension)


@pytest.mark.asyncio
async def test_gateway_tasks_and_identity_are_frozen_at_provider_creation(monkeypatch):
    calls = []

    async def embed(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(dimension=1024, vectors=[[1.0] + [0.01] * 1023])

    monkeypatch.setattr(settings, "embedding_document_task", "retrieval.passage")
    monkeypatch.setattr(settings, "embedding_query_task", "retrieval.query")
    provider = GatewayEmbeddingProvider(gateway=SimpleNamespace(embed=embed))
    identity = provider.space.id
    monkeypatch.setattr(settings, "embedding_model", "jina_ai/jina-embeddings-v3")
    monkeypatch.setattr(settings, "embedding_dimension", 512)
    monkeypatch.setattr(settings, "embedding_model_version", "other")
    await provider.embed(["passage"])
    await provider.embed_query(["query"])
    assert provider.space.id == identity and provider.dimensions == 1024
    assert [call["task"] for call in calls] == ["retrieval.passage", "retrieval.query"]
    assert [call["model"] for call in calls] == ["ollama/bge-m3"] * 2
