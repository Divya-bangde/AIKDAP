import json

import pytest

from app.agents.analytics.graph import get_analytics_graph
from app.agents.analytics.nodes import AnalysisPlanError, AnalyticsGraphDependencies

CSV = b"region,revenue\nWest,100\nEast,300\nWest,50\n"
GOOD_PLAN = {
    "group_by": ["region"],
    "metrics": [{"column": "revenue", "agg": "sum", "alias": "total"}],
    "sort": {"by": "total"},
    "chart": {"type": "bar", "x": "region", "y": ["total"]},
}


class FakeStorage:
    async def read(self, storage_path):
        return CSV


class ScriptedGateway:
    """Returns the queued responses in order; an Exception entry is raised."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def generate(self, **kwargs):
        from app.core.llm.gateway import LLMResponse

        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, model="fake", provider="fake", latency_ms=1)


async def run(gateway, history=None):
    return await get_analytics_graph().ainvoke(
        {"analysis_id": "a1", "question": "Revenue by region?", "storage_path": "p", "file_extension": ".csv", "sheet": None, "history": history or []},
        config={"configurable": {"dependencies": AnalyticsGraphDependencies(storage=FakeStorage(), llm_gateway=gateway)}},
    )


@pytest.mark.asyncio
async def test_happy_path():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East leads with 300, West has 150.")
    state = await run(gateway)
    assert state["result"]["rows"] == [{"region": "East", "total": 300}, {"region": "West", "total": 150}]
    assert state["narrative"].startswith("East leads")
    assert state["unverified_numbers"] == []
    assert state["profile"]["row_count"] == 3


@pytest.mark.asyncio
async def test_invalid_plan_repaired_once():
    bad = {**GOOD_PLAN, "group_by": ["regoin"]}
    gateway = ScriptedGateway(json.dumps(bad), json.dumps(GOOD_PLAN), "East 300.")
    state = await run(gateway)
    assert state["result"]["total_rows"] == 2
    assert "regoin" in gateway.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_plan_still_invalid_fails_run():
    bad = json.dumps({**GOOD_PLAN, "group_by": ["regoin"]})
    with pytest.raises(AnalysisPlanError):
        await run(ScriptedGateway(bad, bad))


@pytest.mark.asyncio
async def test_unanswerable_completes_without_result_or_llm_narrative():
    gateway = ScriptedGateway(json.dumps({"unanswerable_reason": "There is no cost column."}))
    state = await run(gateway)
    assert state["result"] is None
    assert state["narrative"] == "There is no cost column."
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_explain_failure_is_not_fatal():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), RuntimeError("provider down"))
    state = await run(gateway)
    assert state["result"]["total_rows"] == 2
    assert state.get("narrative") is None
    assert "explain_failure" in state["intermediate_results"]


@pytest.mark.asyncio
async def test_invented_number_flagged():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East made 999.")
    assert (await run(gateway))["unverified_numbers"] == ["999"]


@pytest.mark.asyncio
async def test_history_reaches_planner_prompt():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East 300.")
    await run(gateway, history=[{"question": "Total revenue?", "plan": {"metrics": []}}])
    assert "Total revenue?" in gateway.calls[0]["prompt"]
