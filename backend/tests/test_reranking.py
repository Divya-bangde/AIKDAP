"""Sprint 9D: BGE-Reranker-v2-m3 two-stage retrieval unit tests.

Offline throughout. The reranker HTTP endpoint is replaced by an
`httpx.MockTransport` (so `HttpRerankerProvider`'s real request
building, parsing, and error mapping are exercised, not stubbed out),
and the stage-2 provider is faked at the service level where the point
is ordering rather than transport. No test requires Ollama, a running
reranker, or a GPU.

The live-model situation is documented in `reranking.py`: Ollama
0.32.13 cannot serve this cross-encoder, so the real end-to-end
reranking path is exercised here and via a controlled fake, with the
runtime limitation reported rather than worked around.
"""

import json
import uuid

import httpx
import pytest
from pydantic import SecretStr

from app.modules.assets.enums import (
    AssetProcessingStatus,
    AssetSource,
    AssetStatus,
    AssetType,
    EmbeddingStatus,
)
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.models import Asset
from app.modules.auth.models import User
from app.modules.knowledge_base.embeddings import EmbeddingProvider
from app.modules.knowledge_base.enums import EmbeddingProviderName
from app.modules.knowledge_base.models import KnowledgeChunk
from app.modules.knowledge_base.reranking import (
    HttpRerankerProvider,
    RerankCandidate,
    RerankedCandidate,
    RerankerProvider,
    RerankerResponseError,
    RerankerUnavailableError,
    RerankingStatus,
)
from app.modules.knowledge_base.service import (
    KnowledgeBaseService,
    ProjectAccessDeniedError,
)
from app.modules.projects.models import Project, ProjectStatus, ProjectType

DIM = 1024
QUERY = "What challenges does ABC Poultry face in poultry production?"

# Phase 10's controlled candidate set: several chunks of varying
# relevance so reranking is a meaningful operation rather than a no-op.
DOCUMENTS = {
    "A": "ABC Poultry produced 1.2 million tonnes of poultry feed in FY2025.",
    "B": "ABC Poultry operates processing facilities in Maharashtra.",
    "C": "Feed cost inflation is one of the major challenges for poultry businesses.",
    "D": "Farm biosecurity and disease management are important operational concerns.",
    "E": "Consumer demand for processed chicken is expected to influence future growth.",
    "F": "The company is evaluating expansion of poultry processing capacity.",
}


def _unit_vector(hot_index: int, dim: int = DIM) -> list[float]:
    vector = [0.001] * dim
    vector[hot_index] = 1.0
    return vector


class FakeEmbeddingProvider(EmbeddingProvider):
    """Returns a fixed vector, so stage 1 ordering is deterministic."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    @property
    def name(self) -> EmbeddingProviderName:
        return EmbeddingProviderName.LOCAL

    @property
    def dimensions(self) -> int:
        return DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector for _ in texts]


class ScriptedReranker(RerankerProvider):
    """Scores candidates by a caller-supplied content -> score map."""

    def __init__(self, scores_by_content: dict[str, float], *, model: str = "scripted") -> None:
        self._scores = scores_by_content
        self._model = model
        self.calls: list[dict] = []

    @property
    def model(self) -> str:
        return self._model

    async def rerank(self, *, query, candidates, top_k):
        self.calls.append({"query": query, "candidates": candidates, "top_k": top_k})
        scored = [
            RerankedCandidate(
                candidate=candidate,
                rerank_score=self._scores.get(candidate.content, 0.0),
            )
            for candidate in candidates
        ]
        return sorted(scored, key=lambda item: item.rerank_score, reverse=True)[:top_k]


class FailingReranker(RerankerProvider):
    def __init__(self, error: Exception) -> None:
        self._error = error

    @property
    def model(self) -> str:
        return "failing-reranker"

    async def rerank(self, *, query, candidates, top_k):
        raise self._error


def _mock_provider(handler, **overrides) -> HttpRerankerProvider:
    """An `HttpRerankerProvider` whose HTTP layer is a `MockTransport`.

    Injects the transport rather than patching module globals, so the
    provider's real request building, status handling, and response
    parsing all still run.
    """
    kwargs = dict(
        model="qllama/bge-reranker-v2-m3:latest",
        base_url="http://reranker.test",
        endpoint_path="/api/rerank",
        timeout=5,
        max_document_characters=4000,
        transport=httpx.MockTransport(handler),
    )
    kwargs.update(overrides)
    return HttpRerankerProvider(**kwargs)  # type: ignore[arg-type]


def _candidates(contents: list[str]) -> list[RerankCandidate]:
    return [
        RerankCandidate(
            chunk_id=uuid.uuid4(),
            asset_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            chunk_index=index,
            content=content,
            retrieval_distance=0.1 * (index + 1),
        )
        for index, content in enumerate(contents)
    ]


# ---------------------------------------------------------------------------
# TEST 1 & 2 — service initializes, correct model requested
# ---------------------------------------------------------------------------


def test_reranker_initializes_from_configuration():
    """The provider takes its identity and endpoint from settings.

    The model literal this once asserted (`qllama/bge-reranker-v2-m3:
    latest`) was the Ollama registry tag. The same model is now served
    by llama.cpp from its GGUF and is identified as
    `bge-reranker-v2-m3`, so pinning the old string here would assert a
    runtime that no longer exists — and it contradicted the point of
    the test, which is that nothing hardcodes the model. It is checked
    against configuration instead, with the model *family* pinned so a
    silent substitution would still fail.
    """
    from app.core.config import settings

    provider = HttpRerankerProvider()
    assert provider.model == settings.reranker_model
    # Guards the one thing that must never change: this is a
    # BGE-Reranker-v2-m3 deployment, whatever runtime serves it.
    assert "bge-reranker-v2-m3" in provider.model
    assert provider.endpoint.endswith(settings.reranker_endpoint_path)
    assert provider.endpoint.startswith(settings.resolved_reranker_base_url)


@pytest.mark.asyncio
async def test_request_sends_the_configured_model_and_query():
    """TEST 2 + TEST 3: model, query, and documents reach the endpoint."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 1.0}]},
        )

    provider = _mock_provider(handler)
    await provider.rerank(query=QUERY, candidates=_candidates([DOCUMENTS["C"]]), top_k=1)

    assert seen["model"] == "qllama/bge-reranker-v2-m3:latest"
    assert seen["query"] == QUERY
    assert seen["documents"] == [DOCUMENTS["C"]]


@pytest.mark.asyncio
async def test_a_configured_api_key_is_sent_as_a_bearer_token():
    """A hosted reranker (Jina AI) authenticates by bearer token; the
    local llama-server needs none. Same provider, same contract."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    provider = _mock_provider(handler, api_key=SecretStr("jina-test-key"))
    await provider.rerank(query=QUERY, candidates=_candidates([DOCUMENTS["C"]]), top_k=1)

    assert seen["auth"] == "Bearer jina-test-key"


@pytest.mark.asyncio
async def test_an_empty_api_key_sends_no_authorization_header():
    """`RERANKER_API_KEY=` in a .env parses as SecretStr("") rather than
    None, which must not become a bare `Bearer ` sent at llama-server."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    provider = _mock_provider(handler, api_key=SecretStr(""))
    await provider.rerank(query=QUERY, candidates=_candidates([DOCUMENTS["C"]]), top_k=1)

    assert seen["auth"] is None


# ---------------------------------------------------------------------------
# TEST 4 & 5 — scores returned, documents reordered by score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_results_are_reordered_by_rerank_score():
    """The reranker's score, not the input order, decides the output order."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Deliberately score the *last* candidate highest, so passing
        # the test requires real reordering.
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": -2.5},
                    {"index": 1, "relevance_score": 0.4},
                    {"index": 2, "relevance_score": 7.9},
                ]
            },
        )

    provider = _mock_provider(handler)
    candidates = _candidates([DOCUMENTS["A"], DOCUMENTS["B"], DOCUMENTS["C"]])
    ranked = await provider.rerank(query=QUERY, candidates=candidates, top_k=3)

    assert [item.candidate.content for item in ranked] == [
        DOCUMENTS["C"],
        DOCUMENTS["B"],
        DOCUMENTS["A"],
    ]
    assert [item.rerank_score for item in ranked] == [7.9, 0.4, -2.5]


# ---------------------------------------------------------------------------
# TEST 6, 7, 8 — retrieval score, rerank score, and identity all survive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identity_and_both_scores_survive_reranking(session, project):
    chunks = await _seed_chunks(session, project, list(DOCUMENTS.values()))
    reranker = ScriptedReranker(
        {DOCUMENTS["C"]: 9.0, DOCUMENTS["D"]: 8.0, DOCUMENTS["A"]: 1.0}
    )
    service = KnowledgeBaseService(
        session,
        embeddings=FakeEmbeddingProvider(_unit_vector(0)),
        reranker=reranker,
    )

    outcome = await service.two_stage_search(
        project.owner_id, query=QUERY, candidate_k=10, top_k=3
    )

    assert outcome.reranking_status is RerankingStatus.COMPLETED
    known_ids = {chunk.id for chunk in chunks}
    for hit in outcome.hits:
        # TEST 8: chunk/asset identity preserved.
        assert hit.chunk.id in known_ids
        assert hit.chunk.asset_id is not None
        # TEST 6: original retrieval score preserved, untouched.
        assert hit.retrieval_distance is not None
        assert hit.retrieval_rank >= 1
        # TEST 7: rerank score preserved.
        assert hit.rerank_score is not None

    # The two scores are independent measurements, not derived.
    assert outcome.hits[0].rerank_score == 9.0
    assert outcome.hits[0].chunk.content == DOCUMENTS["C"]

    await _cleanup_chunks(session, chunks)


# ---------------------------------------------------------------------------
# TEST 9 & 10 — top_k and candidate_k are respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_k_and_candidate_k_are_respected(session, project):
    chunks = await _seed_chunks(session, project, list(DOCUMENTS.values()))
    reranker = ScriptedReranker({content: float(i) for i, content in enumerate(DOCUMENTS.values())})
    service = KnowledgeBaseService(
        session, embeddings=FakeEmbeddingProvider(_unit_vector(0)), reranker=reranker
    )

    outcome = await service.two_stage_search(
        project.owner_id, query=QUERY, candidate_k=4, top_k=2
    )

    # TEST 10: stage 1 was asked for candidate_k, not top_k -- proving
    # the reranker got a pool larger than the final result set.
    assert outcome.candidate_count == 4
    assert len(reranker.calls[0]["candidates"]) == 4
    # TEST 9: final result limited to top_k.
    assert len(outcome.hits) == 2

    await _cleanup_chunks(session, chunks)


@pytest.mark.asyncio
async def test_candidate_k_below_top_k_is_widened(session, project):
    """A caller asking for more results than candidates must still get
    `top_k` results, not a silently truncated list."""
    chunks = await _seed_chunks(session, project, list(DOCUMENTS.values()))
    reranker = ScriptedReranker({content: 1.0 for content in DOCUMENTS.values()})
    service = KnowledgeBaseService(
        session, embeddings=FakeEmbeddingProvider(_unit_vector(0)), reranker=reranker
    )

    outcome = await service.two_stage_search(
        project.owner_id, query=QUERY, candidate_k=2, top_k=5
    )

    assert outcome.candidate_count == 5
    assert len(outcome.hits) == 5

    await _cleanup_chunks(session, chunks)


# ---------------------------------------------------------------------------
# TEST 11 — empty candidates handled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_candidates_reports_not_applicable(session, project):
    """An empty knowledge base must not be reported as 'reranked'."""
    service = KnowledgeBaseService(
        session,
        embeddings=FakeEmbeddingProvider(_unit_vector(0)),
        reranker=ScriptedReranker({}),
    )

    outcome = await service.two_stage_search(project.owner_id, query=QUERY, top_k=5)

    assert outcome.hits == []
    assert outcome.reranking_status is RerankingStatus.NOT_APPLICABLE
    assert outcome.candidate_count == 0


@pytest.mark.asyncio
async def test_provider_returns_empty_for_empty_candidate_list():
    provider = HttpRerankerProvider(base_url="http://unused.test")
    assert await provider.rerank(query=QUERY, candidates=[], top_k=5) == []


@pytest.mark.asyncio
async def test_empty_query_is_rejected():
    provider = HttpRerankerProvider(base_url="http://unused.test")
    with pytest.raises(RerankerResponseError, match="empty query"):
        await provider.rerank(query="   ", candidates=_candidates(["x"]), top_k=1)


# ---------------------------------------------------------------------------
# TEST 12 — reranker failure is handled safely
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reranker_failure_preserves_retrieval_results(session, project):
    """Stage 2 failing must degrade to stage-1 order, clearly labelled,
    with no fabricated scores."""
    chunks = await _seed_chunks(session, project, list(DOCUMENTS.values()))
    service = KnowledgeBaseService(
        session,
        embeddings=FakeEmbeddingProvider(_unit_vector(0)),
        reranker=FailingReranker(RerankerUnavailableError("endpoint does not exist (HTTP 404)")),
    )

    outcome = await service.two_stage_search(
        project.owner_id, query=QUERY, candidate_k=6, top_k=3
    )

    assert outcome.reranking_status is RerankingStatus.UNAVAILABLE
    assert outcome.reranking_error and "404" in outcome.reranking_error
    # Retrieval results survive intact...
    assert len(outcome.hits) == 3
    # ...with no invented rerank scores.
    assert all(hit.rerank_score is None for hit in outcome.hits)
    assert all(hit.retrieval_distance is not None for hit in outcome.hits)

    await _cleanup_chunks(session, chunks)


@pytest.mark.asyncio
async def test_404_maps_to_unavailable_not_a_response_error():
    """The live Ollama condition: no rerank endpoint at all."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="404 page not found")

    provider = _mock_provider(handler)
    with pytest.raises(RerankerUnavailableError, match="does not implement a rerank API"):
        await provider.rerank(query=QUERY, candidates=_candidates(["x"]), top_k=1)


@pytest.mark.asyncio
async def test_invalid_json_response_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all")

    provider = _mock_provider(handler)
    with pytest.raises(RerankerResponseError):
        await provider.rerank(query=QUERY, candidates=_candidates(["x"]), top_k=1)


@pytest.mark.asyncio
async def test_out_of_range_index_is_rejected():
    """A bad index would misattribute a score to the wrong chunk."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"index": 99, "relevance_score": 1.0}]})

    provider = _mock_provider(handler)
    with pytest.raises(RerankerResponseError, match="out-of-range"):
        await provider.rerank(query=QUERY, candidates=_candidates(["x"]), top_k=1)


@pytest.mark.asyncio
async def test_duplicate_index_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 1.0},
                    {"index": 0, "relevance_score": 2.0},
                ]
            },
        )

    provider = _mock_provider(handler)
    with pytest.raises(RerankerResponseError, match="more than once"):
        await provider.rerank(query=QUERY, candidates=_candidates(["x", "y"]), top_k=2)


@pytest.mark.asyncio
async def test_non_numeric_score_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": "high"}]})

    provider = _mock_provider(handler)
    with pytest.raises(RerankerResponseError, match="non-numeric"):
        await provider.rerank(query=QUERY, candidates=_candidates(["x"]), top_k=1)


# ---------------------------------------------------------------------------
# TEST 13 — ownership filtering preserved through two-stage search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_stage_search_rejects_another_users_project(session, project):
    stranger = User(
        email=f"pytest-stranger-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Stranger",
        is_active=True,
    )
    session.add(stranger)
    await session.flush()
    await session.commit()

    service = KnowledgeBaseService(
        session,
        embeddings=FakeEmbeddingProvider(_unit_vector(0)),
        reranker=ScriptedReranker({}),
    )

    with pytest.raises(ProjectAccessDeniedError):
        await service.two_stage_search(
            stranger.id, query=QUERY, top_k=5, project_id=project.id
        )

    await session.delete(stranger)
    await session.commit()


@pytest.mark.asyncio
async def test_reranker_never_receives_another_users_chunks(session, project):
    """Ownership is applied in stage 1, so stage 2's input is already
    filtered -- the reranker must never even see a stranger's chunk."""
    stranger = User(
        email=f"pytest-stranger-{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Stranger",
        is_active=True,
    )
    session.add(stranger)
    await session.flush()
    stranger_project = Project(
        owner_id=stranger.id,
        name="Stranger's project",
        project_type=ProjectType.RESEARCH,
        status=ProjectStatus.ACTIVE,
    )
    session.add(stranger_project)
    await session.flush()
    await session.commit()

    mine = await _seed_chunks(session, project, ["my private chunk"])
    theirs = await _seed_chunks(session, stranger_project, ["stranger private chunk"])

    reranker = ScriptedReranker({"my private chunk": 1.0, "stranger private chunk": 99.0})
    service = KnowledgeBaseService(
        session, embeddings=FakeEmbeddingProvider(_unit_vector(0)), reranker=reranker
    )

    outcome = await service.two_stage_search(project.owner_id, query=QUERY, top_k=5)

    submitted = {c.content for c in reranker.calls[0]["candidates"]}
    assert submitted == {"my private chunk"}
    assert all(hit.chunk.content == "my private chunk" for hit in outcome.hits)

    await _cleanup_chunks(session, mine + theirs)
    await session.delete(stranger)
    await session.commit()


# ---------------------------------------------------------------------------
# TEST 14 & 15 — duplicates and long content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_content_keeps_distinct_identities():
    """Two chunks with identical text must stay distinguishable: the
    index mapping, not the content, decides which score belongs where."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 1.0},
                    {"index": 1, "relevance_score": 5.0},
                ]
            },
        )

    provider = _mock_provider(handler)
    candidates = _candidates(["identical text", "identical text"])
    ranked = await provider.rerank(query=QUERY, candidates=candidates, top_k=2)

    assert len(ranked) == 2
    assert ranked[0].candidate.chunk_id == candidates[1].chunk_id
    assert ranked[1].candidate.chunk_id == candidates[0].chunk_id
    assert ranked[0].candidate.chunk_id != ranked[1].candidate.chunk_id


@pytest.mark.asyncio
async def test_long_content_is_truncated_to_the_configured_limit():
    """A chunk longer than the cross-encoder budget must be truncated,
    not sent whole and allowed to fail the whole batch."""
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    provider = _mock_provider(handler, max_document_characters=100)
    await provider.rerank(
        query=QUERY, candidates=_candidates(["x" * 5000]), top_k=1
    )

    assert len(sent["documents"][0]) == 100


# ---------------------------------------------------------------------------
# Disabled path — retrieval-only, explicitly labelled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_false_returns_retrieval_order_labelled_disabled(session, project):
    chunks = await _seed_chunks(session, project, list(DOCUMENTS.values()))
    reranker = ScriptedReranker({content: 9.0 for content in DOCUMENTS.values()})
    service = KnowledgeBaseService(
        session, embeddings=FakeEmbeddingProvider(_unit_vector(0)), reranker=reranker
    )

    outcome = await service.two_stage_search(
        project.owner_id, query=QUERY, top_k=3, rerank=False
    )

    assert outcome.reranking_status is RerankingStatus.DISABLED
    assert reranker.calls == [], "the reranker must not be called when disabled"
    assert all(hit.rerank_score is None for hit in outcome.hits)

    await _cleanup_chunks(session, chunks)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_chunks(session, owning_project: Project, contents: list[str]) -> list[KnowledgeChunk]:
    """Create one asset with one embedded chunk per content string."""
    space = FakeEmbeddingProvider(_unit_vector(0)).space
    owning_project.active_embedding_space_id = space.id
    asset = Asset(
        project_id=owning_project.id,
        owner_id=owning_project.owner_id,
        title="Rerank Test Asset",
        asset_type=AssetType.DOCUMENT,
        status=AssetStatus.ACTIVE,
        mime_type="text/plain",
        file_name="doc.txt",
        file_extension=".txt",
        file_size=1,
        storage_path="unused",
        checksum=uuid.uuid4().hex,
        source=AssetSource.UPLOAD,
        version=1,
        tags=[],
        asset_metadata={},
        ai_profile=AIProfile().model_dump(mode="json"),
        created_by=None,
        processing_status=AssetProcessingStatus.COMPLETED,
    )
    session.add(asset)
    await session.flush()

    chunks = []
    for index, content in enumerate(contents):
        chunk = KnowledgeChunk(
            project_id=owning_project.id,
            asset_id=asset.id,
            chunk_index=index,
            content=content,
            embedding_status=EmbeddingStatus.COMPLETED,
            embedding_provider=EmbeddingProviderName.LOCAL,
            embedding=_unit_vector(index),
            embedding_space_id=space.id,
            embedding_space=space.as_dict(),
        )
        session.add(chunk)
        chunks.append(chunk)
    await session.flush()
    await session.commit()
    return chunks


async def _cleanup_chunks(session, chunks: list[KnowledgeChunk]) -> None:
    asset_ids = {chunk.asset_id for chunk in chunks}
    for chunk in chunks:
        await session.delete(chunk)
    await session.flush()
    for asset_id in asset_ids:
        asset = await session.get(Asset, asset_id)
        if asset is not None:
            await session.delete(asset)
    await session.commit()
