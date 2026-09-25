"""Tavily web provider parsing, and synthesis visualization handling."""

import json

import httpx
import pytest
from pydantic import SecretStr

from app.agents.planner.nodes import TavilyWebResearchProvider
from app.agents.planner.synthesis import _parse_response


@pytest.mark.asyncio
async def test_tavily_results_become_real_cited_documents():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Reranking explained",
                        "url": "https://example.org/a",
                        "content": "Cross-encoders score query-document pairs.",
                        "score": 0.91,
                    },
                    {"title": "No text", "url": "https://example.org/b", "content": ""},
                    {"title": "Second", "url": "https://example.org/c", "content": "More.", "score": 0.5},
                ]
            },
        )

    provider = TavilyWebResearchProvider(
        api_key=SecretStr("tvly-test"), timeout=5, transport=httpx.MockTransport(handler)
    )
    documents = await provider.search(query="what is reranking", limit=5)

    assert seen["auth"] == "Bearer tvly-test"
    assert seen["body"]["query"] == "what is reranking"
    assert seen["body"]["max_results"] == 5
    # The result with no text is dropped; the rest keep their order.
    assert [d["reference"] for d in documents] == ["https://example.org/a", "https://example.org/c"]
    assert [d["rank"] for d in documents] == [1, 2]
    assert all(d["simulated"] is False and d["source"] == "web" for d in documents)


@pytest.mark.asyncio
async def test_tavily_http_error_raises_for_the_non_critical_node_to_record():
    provider = TavilyWebResearchProvider(
        api_key=SecretStr("bad"),
        timeout=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "no"})),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await provider.search(query="q", limit=3)


def _response(visualization, **extra) -> str:
    return json.dumps(
        {
            "answer": "Feed costs rose [c1].",
            "citation_ids": ["c1"],
            "grounding_status": "grounded",
            "claims": [],
            "visualization": visualization,
            **extra,
        }
    )


def test_valid_chart_spec_is_kept():
    spec = {
        "kind": "chart2d",
        "title": "Feed cost",
        "data": [{"type": "bar", "x": ["2025", "2026"], "y": [10, 12]}],
    }
    *_, visualization = _parse_response(_response(spec))
    assert visualization["kind"] == "chart2d"
    assert visualization["data"][0]["y"] == [10, 12]


def test_valid_diagram_spec_is_kept():
    spec = {"kind": "diagram", "title": "Pipeline", "mermaid": "flowchart TD\n  A-->B"}
    *_, visualization = _parse_response(_response(spec))
    assert visualization["mermaid"].startswith("flowchart TD")


@pytest.mark.parametrize(
    "spec",
    [
        {"kind": "chart3d", "title": "No traces", "data": []},
        {"kind": "diagram", "title": "No source"},
        {"kind": "hologram", "data": [{"x": [1]}]},
    ],
)
def test_invalid_visualization_is_dropped_without_failing_the_answer(spec):
    answer, *_, visualization = _parse_response(_response(spec))
    assert answer == "Feed costs rose [c1]."
    assert visualization is None


def test_no_visualization_when_none_was_requested():
    *_, visualization = _parse_response(_response(None))
    assert visualization is None


@pytest.mark.parametrize("value", ["on_topic", "related", "off_topic"])
def test_valid_topic_relation_is_kept(value):
    *_, topic_relation, _ = _parse_response(_response(None, topic_relation=value))
    assert topic_relation == value


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({}, id="missing"),
        pytest.param({"topic_relation": None}, id="null"),
        pytest.param({"topic_relation": "unrelated"}, id="unknown-value"),
        pytest.param({"topic_relation": "OFF_TOPIC"}, id="wrong-case"),
        pytest.param({"topic_relation": 3}, id="not-a-string"),
        pytest.param({"topic_relation": ["off_topic"]}, id="list"),
    ],
)
def test_missing_or_invalid_topic_relation_is_none_without_failing_the_answer(extra):
    answer, *_, topic_relation, _ = _parse_response(_response(None, **extra))
    assert answer == "Feed costs rose [c1]."
    assert topic_relation is None


# A small model often writes chart data as a list of points instead of
# Plotly traces. Plotly draws nothing from that, so it must be turned into
# one trace -- or the spec dropped -- never stored as an empty chart.


def test_point_list_is_converted_to_one_trace():
    spec = {
        "kind": "chart2d",
        "title": "Papers per year",
        "data": [{"x": 2018, "y": 1240}, {"x": 2019, "y": 2890}, {"x": 2020, "y": 5310}],
    }
    *_, visualization = _parse_response(_response(spec))
    assert visualization["data"] == [
        {"type": "scatter", "mode": "lines+markers", "x": [2018, 2019, 2020], "y": [1240, 2890, 5310]}
    ]


def test_point_list_with_category_labels_becomes_a_bar_trace():
    spec = {"kind": "chart2d", "data": [{"x": "SGD", "y": 0.79}, {"x": "Adam", "y": 0.86}]}
    *_, visualization = _parse_response(_response(spec))
    assert visualization["data"] == [{"type": "bar", "x": ["SGD", "Adam"], "y": [0.79, 0.86]}]


def test_3d_point_list_becomes_a_scatter3d_trace():
    spec = {"kind": "chart3d", "data": [{"x": 1, "y": 2, "z": 3}, {"x": 4, "y": 5, "z": 6}]}
    *_, visualization = _parse_response(_response(spec))
    assert visualization["data"] == [
        {"type": "scatter3d", "mode": "markers", "x": [1, 4], "y": [2, 5], "z": [3, 6]}
    ]


@pytest.mark.parametrize(
    "data",
    [
        [{"type": "bar", "name": "Papers"}],
        [{"type": "bar", "x": 2018, "y": 1240}],
    ],
)
def test_chart_with_nothing_to_draw_is_dropped(data):
    *_, visualization = _parse_response(_response({"kind": "chart2d", "data": data}))
    assert visualization is None


def test_stored_point_list_is_repaired_when_a_run_is_read():
    """Runs saved before the fix still hold the point-list shape."""
    import uuid
    from datetime import UTC, datetime

    from app.modules.research.schemas import ResearchRunRead

    now = datetime.now(UTC)
    run = ResearchRunRead.model_validate(
        {
            "id": uuid.uuid4(), "project_id": uuid.uuid4(), "owner_id": uuid.uuid4(), "task_id": None,
            "query": "Papers per year?", "status": "completed", "include_assets": True, "include_web": False,
            "max_results": 5, "objective": None, "plan": None, "final_answer": "…", "citations": [],
            "grounding_status": "grounded", "error_message": None, "celery_task_id": None,
            "started_at": now, "completed_at": now, "duration_ms": 1, "created_at": now, "updated_at": now,
            "visualization": {"kind": "chart2d", "title": "T", "data": [{"x": 2018, "y": 1}, {"x": 2019, "y": 2}], "layout": {}, "mermaid": None},
        }
    )
    assert run.visualization["data"] == [{"type": "scatter", "mode": "lines+markers", "x": [2018, 2019], "y": [1, 2]}]
