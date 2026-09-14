"""OpenAlex settings, provider, abstract reconstruction, and gap detection
(Milestone 10 step 2 -- spec section 2)."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config.settings import Settings


def test_openalex_settings_default_to_unconfigured(monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.openalex_api_key is None
    assert settings.openalex_timeout == 20.0


def test_blank_openalex_key_is_treated_as_unset():
    assert Settings.blank_openalex_key_is_unset("") is None
    assert Settings.blank_openalex_key_is_unset("   ") is None
    assert Settings.blank_openalex_key_is_unset("real-key") == "real-key"
    assert Settings.blank_openalex_key_is_unset(None) is None


# ---------------------------------------------------------------------------
# OpenAlexProvider
# ---------------------------------------------------------------------------

from app.agents.planner.paper_suggestion import (  # noqa: E402
    GapDetector,
    OpenAlexProvider,
    _parse_gaps,
    _reconstruct_abstract,
    build_relevance_note,
    build_search_query,
)
from app.modules.research.schemas import GapClassification, GapDetectionResponse, ResearchGap  # noqa: E402


def _work(**overrides):
    base = {
        "id": "https://openalex.org/W123",
        "display_name": "Deep Learning for Poultry Disease Detection",
        "publication_year": 2023,
        "cited_by_count": 42,
        "authorships": [
            {"author": {"display_name": "A. Researcher"}},
            {"author": {"display_name": "B. Scientist"}},
        ],
        "primary_location": {"landing_page_url": "https://example.org/w123"},
        "best_oa_location": {"pdf_url": "https://example.org/w123.pdf"},
        "abstract_inverted_index": {"Cross-encoders": [0], "score": [1], "pairs.": [2]},
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_openalex_results_are_parsed_into_suggested_papers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search"] == "poultry disease detection"
        assert request.url.params["per_page"] == "5"
        return httpx.Response(200, json={"results": [_work()]})

    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5, transport=httpx.MockTransport(handler)
    )
    papers = await provider.search(query="poultry disease detection", limit=5)

    assert len(papers) == 1
    paper = papers[0]
    assert paper["openalex_id"] == "https://openalex.org/W123"
    assert paper["title"] == "Deep Learning for Poultry Disease Detection"
    assert paper["authors"] == ["A. Researcher", "B. Scientist"]
    assert paper["year"] == 2023
    assert paper["cited_by_count"] == 42
    assert paper["landing_url"] == "https://example.org/w123"
    assert paper["oa_pdf_url"] == "https://example.org/w123.pdf"


@pytest.mark.asyncio
async def test_result_with_no_id_or_title_is_dropped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [_work(id=None), _work(display_name=None)]})

    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5, transport=httpx.MockTransport(handler)
    )
    papers = await provider.search(query="q", limit=5)
    assert papers == []


@pytest.mark.asyncio
async def test_abstract_is_rebuilt_from_the_inverted_index_into_the_relevance_note():
    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"),
        timeout=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [_work()]})),
    )
    papers = await provider.search(query="q", limit=5)

    assert "Cross-encoders score pairs." in papers[0]["relevance_note"]


@pytest.mark.asyncio
async def test_paper_with_no_abstract_gets_a_plain_relevance_note():
    work = _work(abstract_inverted_index=None)
    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"),
        timeout=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"results": [work]})),
    )
    papers = await provider.search(query="q", limit=5)

    assert papers[0]["relevance_note"] == "No abstract available from OpenAlex."


@pytest.mark.asyncio
async def test_openalex_timeout_raises_for_the_non_critical_node_to_record():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(httpx.TimeoutException):
        await provider.search(query="q", limit=5)


@pytest.mark.asyncio
async def test_openalex_http_error_raises_for_the_non_critical_node_to_record():
    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"),
        timeout=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "down"})),
    )
    # A scrubbed RuntimeError, not the raw httpx.HTTPStatusError -- whose
    # message embeds the request URL, api_key included.
    with pytest.raises(RuntimeError, match="500"):
        await provider.search(query="q", limit=5)


@pytest.mark.asyncio
async def test_query_wildcards_are_stripped_before_reaching_openalex():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "?" not in request.url.params["search"]
        assert "*" not in request.url.params["search"]
        return httpx.Response(200, json={"results": [_work()]})

    provider = OpenAlexProvider(
        api_key=SecretStr("test-key"), timeout=5, transport=httpx.MockTransport(handler)
    )
    papers = await provider.search(query="What improves detection? *", limit=5)

    assert len(papers) == 1
    assert papers[0]["title"] == "Deep Learning for Poultry Disease Detection"


def test_reconstruct_abstract_orders_words_by_position():
    inverted_index = {"score.": [2], "Cross-encoders": [0], "pairs": [1]}
    assert _reconstruct_abstract(inverted_index) == "Cross-encoders pairs score."


def test_reconstruct_abstract_handles_missing_index():
    assert _reconstruct_abstract(None) == ""
    assert _reconstruct_abstract({}) == ""


def test_get_paper_provider_returns_none_without_a_key(monkeypatch):
    from app.agents.planner.nodes import get_paper_provider
    from app.core.config.settings import settings

    monkeypatch.setattr(settings, "openalex_api_key", None)
    assert get_paper_provider() is None


def test_get_paper_provider_returns_a_configured_provider_with_a_key(monkeypatch):
    from app.agents.planner.nodes import get_paper_provider
    from app.core.config.settings import settings

    monkeypatch.setattr(settings, "openalex_api_key", SecretStr("test-key"))
    provider = get_paper_provider()
    assert isinstance(provider, OpenAlexProvider)


# ---------------------------------------------------------------------------
# Search query and relevance note
# ---------------------------------------------------------------------------


def _gap(**overrides) -> ResearchGap:
    base = dict(
        gap_type="baseline comparison",
        classification=GapClassification.REQUIRED,
        description="a comparison against a non-deep-learning baseline",
        why_needed="to judge whether the reported gain is real",
    )
    base.update(overrides)
    return ResearchGap(**base)


def test_build_search_query_combines_question_and_gap_descriptions():
    gaps = [
        _gap(description="a comparison against a non-deep-learning baseline"),
        _gap(gap_type="dataset size", description="the training set's total sample count"),
    ]
    query = build_search_query("What improves poultry disease detection accuracy?", gaps)
    assert query == (
        "What improves poultry disease detection accuracy? "
        "a comparison against a non-deep-learning baseline "
        "the training set's total sample count"
    )


def test_build_relevance_note_summarizes_the_gaps():
    note = build_relevance_note([_gap()])
    assert note == "Suggested to help address: a comparison against a non-deep-learning baseline"


# ---------------------------------------------------------------------------
# GapDetector
# ---------------------------------------------------------------------------


class FakeGateway:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls.append(kwargs)
        return LLMResponse(content=self._content, model="fake-model", provider="fake", latency_ms=5)


@pytest.mark.asyncio
async def test_gap_detector_parses_the_model_json_schema_response():
    content = json.dumps(
        {
            "gaps": [
                {
                    "gap_type": "baseline comparison",
                    "classification": "required",
                    "description": "no comparison to a simple baseline",
                    "why_needed": "to know if the improvement is meaningful",
                    "search_intent": "poultry disease detection baseline comparison",
                }
            ]
        }
    )
    detector = GapDetector(gateway=FakeGateway(content), model="fake-model")
    gaps = await detector.detect(query="q", answer="an answer [c1].", grounding_status="grounded")

    assert len(gaps) == 1
    assert gaps[0].gap_type == "baseline comparison"
    assert gaps[0].classification is GapClassification.REQUIRED
    assert gaps[0].search_intent == "poultry disease detection baseline comparison"


@pytest.mark.asyncio
async def test_gap_detector_returns_no_gaps_when_the_model_reports_none():
    detector = GapDetector(gateway=FakeGateway(json.dumps({"gaps": []})), model="fake-model")
    assert await detector.detect(query="q", answer="a complete answer.", grounding_status="grounded") == []


@pytest.mark.asyncio
async def test_gap_detector_returns_no_gaps_for_an_empty_answer_without_calling_the_model():
    gateway = FakeGateway(json.dumps({"gaps": []}))
    detector = GapDetector(gateway=gateway, model="fake-model")

    assert await detector.detect(query="q", answer="", grounding_status="insufficient_evidence") == []
    assert gateway.calls == []


def test_parse_gaps_degrades_to_empty_list_on_unparseable_content():
    assert _parse_gaps("not json") == []


def test_parse_gaps_degrades_to_empty_list_on_schema_mismatch():
    assert _parse_gaps(json.dumps({"gaps": [{"gap_type": "x"}]})) == []


def test_parse_gaps_strips_a_json_fence():
    content = "```json\n" + json.dumps({"gaps": []}) + "\n```"
    assert _parse_gaps(content) == []


def test_gap_detection_response_reuses_the_existing_research_gap_shape():
    response = GapDetectionResponse.model_validate({"gaps": [_gap().model_dump()]})
    assert response.gaps[0].gap_type == "baseline comparison"
