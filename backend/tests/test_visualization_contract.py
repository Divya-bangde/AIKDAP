"""Offline adversarial contract tests; fixtures are synthetic, never model evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.agents.planner.visualization import accept_visualization, verify_values
from app.modules.research import visualization_contract as contract
from app.modules.research.visualization_contract import Visualization

CITATION = {"id": "c1", "reference": "asset:a#chunk-0", "asset_id": "a", "chunk_id": "chunk", "simulated": False, "snippet": "Category A: 42 kg."}


def spec(value=42, origin="measured"):
    return {"kind": "chart2d", "origin": origin, "data": [{"type": "bar", "x": ["Category A"], "y": [value]}], "provenance": [{"trace": 0, "field": "y", "point": 0, "formula": "identity", "unit": "kg", "inputs": [{"citation_id": "c1", "reference": CITATION["reference"], "chunk_id": "chunk", "asset_id": "a", "quote": CITATION["snippet"], "token_index": 0, "value": 42, "unit": "kg"}]}]}


def accept(raw, query="Show a bar chart"):
    return accept_visualization(raw=raw, query=query, supplied=[CITATION], full_text={"chunk": CITATION["snippet"]})


def test_42_does_not_authorize_999999():
    visual, outcome = accept(spec(999999))
    assert visual is None
    assert outcome["status"] == "failed"
    assert "independently computed" in outcome["reason"]


def test_exact_point_verified_and_illustrative_origin_retained():
    visual, outcome = accept(spec())
    assert visual["data"][0]["y"] == [42]
    assert outcome["verification"] == "verified"
    visual, outcome = accept(spec(origin="illustrative"))
    assert visual["origin"] == "illustrative"
    assert outcome["verification"] == "illustrative"


def test_supported_conversion_traceable_and_wrong_units_rejected():
    raw = spec(42000, "derived")
    raw["provenance"][0].update(formula="convert", unit="g")
    visual, outcome = accept(raw)
    assert visual["provenance"][0]["inputs"][0]["value"] == 42
    assert outcome["verification"] == "verified"
    raw["provenance"][0]["unit"] = "m"
    assert accept(raw)[0] is None


@pytest.mark.parametrize("field,value", [("asset_id", "foreign"), ("chunk_id", "foreign"), ("reference", "asset:foreign"), ("citation_id", "foreign"), ("value", 999999), ("unit", "m"), ("quote", "Category A: 999999 kg.")])
def test_foreign_or_self_asserted_evidence_rejected(field, value):
    raw = spec()
    raw["provenance"][0]["inputs"][0][field] = value
    assert accept(raw)[0] is None


def test_unresolved_chunk_and_missing_or_duplicate_provenance_rejected():
    assert accept_visualization(raw=spec(), query="bar chart", supplied=[CITATION], full_text={})[0] is None
    raw = spec(); raw["provenance"] = []
    assert accept(raw)[0] is None
    raw = spec(); raw["provenance"] *= 2
    assert accept(raw)[0] is None


def test_missing_visual_and_intent_mismatch_have_independent_completion():
    assert accept(None)[1]["status"] == "not_enough_data"
    assert accept(spec(), "Show a line chart")[0] is None
    assert accept_visualization(raw=None, query="Explain feed", supplied=[], full_text={}) == (None, None)


def test_numeric_year_bar_preserves_requested_intent_with_one_bounded_normalization():
    raw = {"kind": "chart2d", "data": [{"x": 2025, "y": 42}], "intent": {"type": "bar"}}
    parsed = Visualization.model_validate(raw)
    assert parsed.data[0].type == "bar"
    raw.pop("intent")
    with pytest.raises(ValidationError, match="explicit"):
        Visualization.model_validate(raw)
    _, outcome = accept(raw)
    assert outcome["requested"]["type"] == "bar"
    assert outcome["repair_attempts"] == 1


@pytest.mark.parametrize("trace,kind", [
    ({"type": "bar", "name": "metadata"}, "chart2d"),
    ({"type": "scatter", "x": [1], "y": [2, 3]}, "chart2d"),
    ({"type": "scatter", "x": [1], "y": [float("nan")]}, "chart2d"),
    ({"type": "scatter", "x": [1], "y": [float("inf")]}, "chart2d"),
    ({"type": "scatter", "x": [1], "y": [2]}, "chart3d"),
    ({"type": "scatter3d", "x": [1], "y": [2]}, "chart3d"),
    ({"type": "heatmap", "z": [[1,2],[3]]}, "chart2d"),
    ({"type": "surface", "x": [1], "z": [[1,2]]}, "chart3d"),
    ({"type": "pie", "labels": ["A"], "values": [0]}, "chart2d"),
    ({"type": "pie", "labels": ["A"], "values": [-1]}, "chart2d"),
    ({"type": "scatter", "x": [1,"A"], "y": [1,2]}, "chart2d"),
])
def test_invalid_trace_rejected(trace, kind):
    with pytest.raises(ValidationError):
        Visualization.model_validate({"kind": kind, "data": [trace]})


@pytest.mark.parametrize("trace,kind", [
    ({"type":"bar","x":["A"],"y":[1]},"chart2d"),
    ({"type":"scatter","x":[1],"y":[2]},"chart2d"),
    ({"type":"pie","labels":["A"],"values":[1]},"chart2d"),
    ({"type":"heatmap","z":[[1,2],[3,4]]},"chart2d"),
    ({"type":"surface","z":[[1,2],[3,4]]},"chart3d"),
    ({"type":"scatter3d","x":[1],"y":[2],"z":[3]},"chart3d"),
])
def test_supported_trace_retained(trace, kind):
    assert Visualization.model_validate({"kind": kind, "data": [trace]}).data[0].type == trace["type"]


def test_layout_allowlist_and_dates():
    raw = {"kind":"chart2d","data":[{"type":"scatter","x":["2026-99-01"],"y":[2]}],"layout":{"xaxis":{"type":"date"}}}
    with pytest.raises(ValidationError, match="valid ISO"):
        Visualization.model_validate(raw)
    raw["layout"] = {"javascript": "untrusted"}
    with pytest.raises(ValidationError):
        Visualization.model_validate(raw)


def test_limits_at_and_above_trace_point_label_and_grid_budgets():
    trace = {"type":"bar","x":["A"] * contract.MAX_POINTS,"y":[1] * contract.MAX_POINTS}
    assert Visualization.model_validate({"kind":"chart2d","data":[trace]})
    trace["x"].append("A"); trace["y"].append(1)
    with pytest.raises(ValidationError): Visualization.model_validate({"kind":"chart2d","data":[trace]})
    trace = {"type":"bar","x":["A"],"y":[1],"name":"A" * contract.MAX_LABEL}
    assert Visualization.model_validate({"kind":"chart2d","data":[trace] * contract.MAX_TRACES})
    with pytest.raises(ValidationError): Visualization.model_validate({"kind":"chart2d","data":[trace] * (contract.MAX_TRACES + 1)})
    trace["name"] += "A"
    with pytest.raises(ValidationError): Visualization.model_validate({"kind":"chart2d","data":[trace]})
    width = 200
    assert Visualization.model_validate({"kind":"chart2d","data":[{"type":"heatmap","z":[[1]*width]*(contract.MAX_GRID//width)}]})
    with pytest.raises(ValidationError): Visualization.model_validate({"kind":"chart2d","data":[{"type":"heatmap","z":[[1]*width]*(contract.MAX_GRID//width+1)}]})


def test_total_and_serialized_byte_limits(monkeypatch):
    monkeypatch.setattr(contract, "MAX_TOTAL", 4)
    trace = {"type":"scatter","x":[1,2],"y":[1,2]}
    assert Visualization.model_validate({"kind":"chart2d","data":[trace,trace]})
    with pytest.raises(ValidationError, match="total points"):
        Visualization.model_validate({"kind":"chart2d","data":[trace,trace,trace]})
    raw = {"kind":"chart2d","data":[trace]}
    size = len(json.dumps(raw, ensure_ascii=False).encode())
    monkeypatch.setattr(contract,"MAX_BYTES",size)
    assert Visualization.model_validate(raw)
    monkeypatch.setattr(contract,"MAX_BYTES",size-1)
    with pytest.raises(ValidationError, match="serialized bytes"): Visualization.model_validate(raw)


@pytest.mark.asyncio
async def test_full_evidence_resolver_checks_project_and_asset():
    from app.agents.planner.nodes import _resolve_full_evidence_text
    import uuid
    chunk_id = str(uuid.uuid4())
    citation = {**CITATION, "chunk_id": chunk_id}
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(asset_id="a",project_id="foreign",content="42")))
    assert await _resolve_full_evidence_text(repo,[citation],project_id="allowed") == {}
    repo.get_by_id.return_value.project_id = "allowed"
    assert await _resolve_full_evidence_text(repo,[citation],project_id="allowed") == {chunk_id: "42"}
    repo.get_by_id.return_value.asset_id = "foreign"
    assert await _resolve_full_evidence_text(repo,[citation],project_id="allowed") == {}


def test_foreign_category_number_in_same_quote_cannot_authorize_point():
    raw = spec()
    citation = {**CITATION, "snippet": "East 10 kg; West 42 kg."}
    raw["data"][0]["x"] = ["East"]
    raw["provenance"][0]["inputs"][0].update(quote=citation["snippet"], token_index=1)
    visual, outcome = accept_visualization(raw=raw, query="bar chart", supplied=[citation], full_text={"chunk":citation["snippet"]})
    assert visual is None
    assert "associate" in outcome["reason"]
    citation["snippet"] = "East 10 kg West 42 kg."
    raw["provenance"][0]["inputs"][0]["quote"] = citation["snippet"]
    assert accept_visualization(raw=raw, query="bar chart", supplied=[citation], full_text={"chunk":citation["snippet"]})[0] is None


def test_actual_illustrative_source_cannot_be_promoted_to_measured():
    raw = spec()
    citation = {**CITATION, "snippet": "Illustrative sample: Category A: 42 kg."}
    visual, outcome = accept_visualization(raw=raw, query="bar chart", supplied=[citation], full_text={"chunk":citation["snippet"]})
    assert visual["origin"] == "illustrative"
    assert outcome["verification"] == "illustrative"


@pytest.mark.parametrize("kind,trace,layout", [
    ("chart2d", {"type":"scatter","x":["category"],"y":[1]}, {"xaxis":{"type":"linear"}}),
    ("chart2d", {"type":"bar","x":["A"],"y":[-1]}, {"yaxis":{"type":"log"}}),
    ("chart3d", {"type":"surface","x":["A"],"z":[[1]]}, {"scene":{"xaxis":{"type":"linear"}}}),
    ("chart3d", {"type":"surface","z":[[0]]}, {"scene":{"zaxis":{"type":"log"}}}),
])
def test_explicit_axis_semantics_cannot_hide_or_reinterpret_observations(kind, trace, layout):
    with pytest.raises(ValidationError):
        Visualization.model_validate({"kind":kind,"data":[trace],"layout":layout})


def test_category_substring_is_not_an_observation_identity():
    raw = spec()
    raw["data"][0]["x"] = ["A"]
    citation = {**CITATION,"snippet":"Category Aardvark: 42 kg."}
    raw["provenance"][0]["inputs"][0]["quote"] = citation["snippet"]
    assert accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})[0] is None


def test_source_origin_qualifier_outside_immediate_quote_remains_illustrative():
    raw = spec()
    text = "Illustrative data. " + "Documentation. " * 40 + CITATION["snippet"]
    visual, outcome = accept_visualization(raw=raw, query="bar chart", supplied=[CITATION], full_text={"chunk":text})
    assert visual["origin"] == "illustrative"
    assert outcome["verification"] == "illustrative"


@pytest.mark.asyncio
async def test_invalid_visual_preserves_grounded_prose_and_exposes_outcome():
    from app.agents.planner.synthesis import GroundedSynthesizer
    from app.core.llm.gateway import LLMResponse
    gateway = SimpleNamespace(generate=AsyncMock(return_value=LLMResponse(content=json.dumps({"answer":"Category A is 42 kg [c1].","citation_ids":["c1"],"visualization":spec(999999)}),model="offline-double",provider="fixture",latency_ms=1)))
    result = await GroundedSynthesizer(gateway=gateway).synthesize(query="Show a bar chart",objective="Show values",context="",documents=[],citations=[{**CITATION,"title":"Synthetic fixture","source":"asset","provider":"fixture","score":1}],warnings=[],full_evidence_text={"chunk":CITATION["snippet"]})
    assert result.answer.startswith("Category A is 42 kg")
    assert result.visualization is None
    assert result.visualization_outcome["status"] == "failed"
    assert result.grounding_status.value == "grounded"


@pytest.mark.asyncio
async def test_outcome_persists_through_service_completion_and_read():
    from app.modules.research.service import ResearchExecutionService
    from app.modules.research.schemas import ResearchRunRead
    from datetime import datetime, UTC
    import time
    import uuid
    visual, outcome = accept(spec())
    now = datetime.now(UTC)
    run = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(),owner_id=uuid.uuid4(),task_id=None,query="Show bar chart",include_assets=True,include_web=False,max_results=5,created_at=now,updated_at=now,error_message=None,celery_task_id=None,started_at=now)
    service = SimpleNamespace(_session=SimpleNamespace(commit=AsyncMock()))
    await ResearchExecutionService._complete(service,run,time.monotonic(),{"final_answer":"42 kg","visualization":visual,"visualization_outcome":outcome,"grounding_status":"grounded"})
    read = ResearchRunRead.model_validate(run)
    assert read.visualization.data[0].y == [42]
    assert read.visualization_outcome.status == "produced"
    assert read.visualization_outcome.requested.type == "bar"
    assert read.visualization_outcome.verification == "verified"
    service._session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_visual_empty_evidence_and_extractive_paths_report_status():
    from app.agents.planner.synthesis import GroundedSynthesizer, ExtractiveSynthesizer
    for synthesizer in (GroundedSynthesizer(gateway=SimpleNamespace(generate=AsyncMock())), ExtractiveSynthesizer()):
        result = await synthesizer.synthesize(query="Show a bar chart",objective="show chart",context="",documents=[],citations=[],warnings=[])
        assert result.visualization is None
        assert result.visualization_outcome["status"] == "not_enough_data"
        assert result.visualization_outcome["requested"]["type"] == "bar"


@pytest.mark.parametrize("data", [[1], [None], ["invalid"], [{"x":1,"y":2},None]])
def test_malformed_visual_entries_are_withheld_without_throwing(data):
    visual,outcome=accept({"kind":"chart2d","data":data})
    assert visual is None
    assert outcome["status"] == "failed"


def test_unsupported_requested_type_and_ambiguous_interpretation():
    for raw in (None,spec()):
        visual,outcome=accept(raw,"Show a radar chart")
        assert visual is None
        assert outcome["status"] == "unsupported"
        assert outcome["requested"]["type"] == "radar"
    visual,outcome=accept(spec(),"Show a chart")
    assert visual is not None
    assert outcome["requested"]["interpretation"] == "Interpreted the ambiguous visual request as bar."


def test_swapped_grid_cell_rejected_even_with_real_source_number():
    raw=spec()
    raw["data"]=[{"type":"heatmap","x":["Column A"],"y":["Row A"],"z":[[42]]}]
    point=raw["provenance"][0]
    point.update(field="z",point=0,column=0)
    citation={**CITATION,"snippet":"Column B Row A: 42 kg."}
    point["inputs"][0]["quote"]=citation["snippet"]
    visual,outcome=accept_visualization(raw=raw,query="Show a heatmap",supplied=[citation],full_text={"chunk":citation["snippet"]})
    assert visual is None
    citation["snippet"]="Column A Row A: 42 kg."
    point["inputs"][0]["quote"]=citation["snippet"]
    visual,outcome=accept_visualization(raw=raw,query="Show a heatmap",supplied=[citation],full_text={"chunk":citation["snippet"]})
    assert visual is not None


def test_exact_integer_precision_and_clipping_ranges_rejected():
    for raw in (
        {"kind":"chart2d","data":[{"type":"bar","x":["A"],"y":[9007199254740992]}]},
        {"kind":"chart2d","data":[{"type":"bar","x":["A"],"y":[42]}],"layout":{"yaxis":{"range":[0,1]}}},
        {"kind":"chart2d","data":[{"type":"bar","x":["A"],"y":[42]}],"layout":{"title":{"script":"arbitrary"}}},
    ):
        with pytest.raises(ValidationError): Visualization.model_validate(raw)


def test_diagram_budget_at_and_above(monkeypatch):
    assert Visualization.model_validate({"kind":"diagram","mermaid":"a"*contract.MAX_DIAGRAM})
    with pytest.raises(ValidationError): Visualization.model_validate({"kind":"diagram","mermaid":"a"*(contract.MAX_DIAGRAM+1)})


@pytest.mark.parametrize("axis,semantics,domain,expected", [
    ([3,1,2],"numeric",None,[1,2,3]),
    (["2026-03-01","2026-01-01","2026-02-01"],"date",None,["2026-01-01","2026-02-01","2026-03-01"]),
    (["March","January","February"],"ordinal",None,["January","February","March"]),
    (["high","low","medium"],"ordinal",["low","medium","high"],["low","medium","high"]),
])
def test_research_trend_order_keeps_metrics_gaps_and_provenance_aligned(axis,semantics,domain,expected):
    from app.agents.planner.visualization import order_trends
    raw={"intent":{"axis_semantics":semantics,"domain":domain},"data":[{"type":"scatter","mode":"lines+markers","x":axis,"y":[30,None,20]}],"provenance":[{"trace":0,"field":"y","point":0},{"trace":0,"field":"y","point":2}]}
    order_trends(raw,"show line chart")
    assert raw["data"][0]["x"] == expected
    assert raw["data"][0]["y"] == [None,20,30]
    assert [p["point"] for p in raw["provenance"]] == [2,1]


@pytest.mark.parametrize("axis,semantics,domain", [
    ([1,1],"numeric",None),([None,1],"numeric",None),
    (["bad-date","2026-01-01"],"date",None),
    (["2026-01-01T00:00:00Z","2026-01-01T01:00:00+01:00"],"date",None),
    (["A","B"],"ordinal",None),(["A","B"],"ordinal",["A","A","B"]),
])
def test_research_trend_invalid_or_duplicate_x_rejected(axis,semantics,domain):
    from app.agents.planner.visualization import order_trends
    raw={"intent":{"axis_semantics":semantics,"domain":domain},"data":[{"type":"scatter","mode":"lines","x":axis,"y":[1,2]}],"provenance":[]}
    with pytest.raises(ValueError): order_trends(raw,"show chart")


def test_research_trajectory_and_unclassified_lines_preserve_sequence():
    from app.agents.planner.visualization import order_trends
    for intent,query in (({"axis_semantics":"trajectory"},"show line chart"),({"axis_semantics":"numeric"},"show trajectory line chart"),({},"show line chart")):
        raw={"intent":intent,"data":[{"type":"scatter","mode":"lines","x":[3,1,2],"y":[30,None,20]}],"provenance":[]}
        order_trends(raw,query)
        assert raw["data"][0]["x"] == [3,1,2]
        assert raw["data"][0]["y"] == [30,None,20]


def test_verified_research_numeric_trend_remaps_actual_source_provenance():
    raw={"kind":"chart2d","origin":"measured","intent":{"type":"line","axis_semantics":"numeric"},"data":[{"type":"scatter","mode":"lines+markers","x":[2025,2023,2024],"y":[42,None,20]}],"provenance":[]}
    quotes=["2025: 42 kg", "2023: no observation", "2024: 20 kg"]
    for point in range(3):
        for field,index,value,unit in (("x",0,raw["data"][0]["x"][point],""),("y",1,raw["data"][0]["y"][point],"kg")):
            if value is None: continue
            source={"citation_id":"c1","reference":CITATION["reference"],"chunk_id":"chunk","asset_id":"a","quote":quotes[point],"token_index":index,"value":value,"unit":unit}
            raw["provenance"].append({"trace":0,"field":field,"point":point,"unit":unit,"formula":"identity","inputs":[source]})
    citation={**CITATION,"snippet":"; ".join(quotes)}
    visual,outcome=accept_visualization(raw=raw,query="Show annual trend line chart",supplied=[citation],full_text={"chunk":citation["snippet"]})
    assert visual["data"][0]["x"] == [2023,2024,2025]
    assert visual["data"][0]["y"] == [None,20,42]
    assert visual["data"][0]["connectgaps"] is False
    verify_values(visual,[citation],{"chunk":citation["snippet"]})


def test_research_declared_month_gap_cannot_be_bridged():
    from app.agents.planner.visualization import order_trends
    raw={"intent":{"axis_semantics":"ordinal"},"data":[{"type":"scatter","mode":"lines","x":["March","January"],"y":[30,10]}],"provenance":[]}
    with pytest.raises(ValueError,match="explicit null"):
        order_trends(raw,"monthly trend chart")
    raw["data"][0].update(x=["March","January","February"],y=[30,10,None])
    order_trends(raw,"monthly trend chart")
    assert raw["data"][0]["x"] == ["January","February","March"]
    assert raw["data"][0]["y"] == [10,None,30]


def test_intent_detection_avoids_incidental_words_and_preserves_explicit_line():
    from app.agents.planner.visualization import requested_intent
    assert requested_intent("Explain surface tension") is None
    assert requested_intent("Why did bar pressure rise?") is None
    assert requested_intent("Show bar pressure as a line chart").type == "line"


@pytest.mark.parametrize("label,quote,coordinate_value",[(2024,"2024: 42 kg.",2024),("Class 2","Class 2: 42 kg.",2)])
def test_coordinate_number_cannot_be_reused_as_observation_or_derived_input(label,quote,coordinate_value):
    citation={**CITATION,"snippet":quote}
    raw=spec(coordinate_value)
    raw["data"][0]["x"]=[label]
    ref=raw["provenance"][0]
    ref["unit"]=""
    ref["inputs"][0].update(quote=quote,token_index=0,value=coordinate_value,unit="")
    if isinstance(label,int):
        xref=deepcopy(ref); xref["field"]="x"
        raw["provenance"].append(xref)
    visual,outcome=accept_visualization(raw=raw,query="Show a bar chart",supplied=[citation],full_text={"chunk":quote})
    assert visual is None
    assert "overlaps" in outcome["reason"]
    raw["origin"]="derived"
    ref["formula"]="sum"
    visual,outcome=accept_visualization(raw=raw,query="Show a bar chart",supplied=[citation],full_text={"chunk":quote})
    assert visual is None and "overlaps" in outcome["reason"]
    raw["origin"]="measured"
    raw["data"][0]["y"]=[42]
    ref.update(formula="identity",unit="kg")
    ref["inputs"][0].update(token_index=1,value=42,unit="kg")
    visual,outcome=accept_visualization(raw=raw,query="Show a bar chart",supplied=[citation],full_text={"chunk":quote})
    assert visual["data"][0]["y"]==[42]
    assert outcome["verification"]=="verified"


def test_equal_numeric_coordinate_and_observation_require_distinct_actual_source_tokens():
    quote="2024: 2024 kg."
    citation={**CITATION,"snippet":quote}
    raw=spec(2024)
    raw["data"][0]["x"]=[2024]
    yref=raw["provenance"][0]
    yref["inputs"][0].update(quote=quote,token_index=1,value=2024)
    xref=deepcopy(yref); xref.update(field="x",unit="")
    xref["inputs"][0].update(token_index=0,unit="")
    raw["provenance"].append(xref)
    visual,outcome=accept_visualization(raw=raw,query="Show a bar chart",supplied=[citation],full_text={"chunk":quote})
    assert visual is not None
    assert outcome["verification"]=="verified"


@pytest.mark.parametrize("uid",['[quoted"trace]','trace space','trace.1','trace:1','étrace',''])
def test_provided_trace_uid_must_be_ascii_css_safe(uid):
    raw=spec(); raw["data"][0]["uid"]=uid
    visual,outcome=accept(raw)
    assert visual is None
    assert outcome["status"]=="failed"


def test_safe_uids_preserved_and_duplicate_provided_uids_rejected():
    raw=spec(); raw["data"][0]["uid"]="trace-01_Revenue"
    assert Visualization.model_validate(raw).data[0].uid=="trace-01_Revenue"
    raw["data"].append(deepcopy(raw["data"][0]))
    with pytest.raises(ValidationError,match="unique"):
        Visualization.model_validate(raw)
    visual,outcome=accept(raw)
    assert visual is None and "unique" in outcome["reason"]



def scientific_chart(token,claimed,plotted,formula="identity",origin="measured"):
    quote=f"Category A: {token} kg"
    citation={**CITATION,"snippet":quote}
    raw=spec(plotted,origin=origin)
    raw["provenance"][0]["inputs"][0].update(quote=quote,value=claimed)
    raw["provenance"][0]["formula"]=formula
    return raw,citation


@pytest.mark.parametrize("token,claimed,plotted",[
    ("1e-20",1e-20,0.0),("1e-20",1e-20,1e-13),
    ("1e-324",0.0,0.0),
    ("9007199254740993",9007199254740992.0,9007199254740992.0),
    ("1000000000000000",1e15,1e15+100),
    ("1000000000000000",1e15,1e15+1000),
    ("0",0.0,1e-20),
])
def test_exact_source_identity_cannot_be_erased_underflowed_or_reinterpreted(token,claimed,plotted):
    raw,citation=scientific_chart(token,claimed,plotted)
    visual,outcome=accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})
    assert visual is None
    assert outcome["status"]=="failed"


@pytest.mark.parametrize("value",[1e-20,1e-100,1e-300,1e-323,0.0,1e15])
def test_valid_small_scientific_and_safe_large_identity_remains_usable(value):
    raw,citation=scientific_chart(str(value),value,value)
    visual,outcome=accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})
    assert visual is not None
    assert visual["data"][0]["y"]==[value]


def test_derived_floating_arithmetic_allows_one_ulp_without_a_zero_floor():
    raw,citation=scientific_chart("0.1",0.1,0.1+0.2,formula="sum",origin="derived")
    second=deepcopy(raw["provenance"][0]["inputs"][0]); second.update(value=0.2,quote="Category A: 0.2 kg")
    raw["provenance"][0]["inputs"].append(second)
    text=citation["snippet"]+"; "+second["quote"]
    visual,outcome=accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":text})
    assert visual is not None
    for bad in (0.0,0.300000000001):
        raw["data"][0]["y"]=[bad]
        assert accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":text})[0] is None
    raw,citation=scientific_chart("1e-323",1e-323,0.0,formula="sum",origin="derived")
    assert accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})[0] is None


def test_explicit_rounding_is_decimal_ties_even_and_requires_derived_origin():
    raw,citation=scientific_chart("2.675",2.675,2.68,origin="derived")
    raw["provenance"][0]["rounding_decimals"]=2
    assert accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})[0] is not None
    raw["data"][0]["y"]=[2.67]
    assert accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})[0] is None
    raw["data"][0]["y"]=[2.68]; raw["origin"]="measured"
    assert accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})[0] is None


@pytest.mark.parametrize("token,claimed",[
    ("1e9999999999",1.0),("-1e9999999999",-1.0),
    ("1e-9999999999",0.0),("-1e-9999999999",0.0),
    ("1e99999999999999999999999999999999999999",1.0),
    ("1e-99999999999999999999999999999999999999",0.0),
])
def test_extreme_source_exponents_fail_closed_before_fraction_allocation(token,claimed,monkeypatch):
    import app.agents.planner.visualization as module
    from unittest.mock import Mock
    raw,citation=scientific_chart(token,claimed,claimed)
    fraction=Mock(side_effect=AssertionError("unbounded source token reached Fraction allocation"))
    monkeypatch.setattr(module,"Fraction",fraction)
    visual,outcome=accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})
    assert visual is None and outcome["status"]=="failed"
    fraction.assert_not_called()


@pytest.mark.parametrize("token",["0e9999999999","0e-9999999999","-0e-9999999999"])
def test_extreme_zero_exponents_normalize_without_large_denominator(token):
    raw,citation=scientific_chart(token,0.0,0.0)
    visual,outcome=accept_visualization(raw=raw,query="bar chart",supplied=[citation],full_text={"chunk":citation["snippet"]})
    assert visual is not None and outcome["verification"]=="verified"
