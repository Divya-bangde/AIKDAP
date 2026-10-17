"""Synthetic experiment charts: strict schema, preflight and actionable route failures."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.modules.research import visualization_contract as budgets
from app.modules.research.experiment_schemas import VisualizationData, VisualizationSeries, ExperimentTestCase, ExperimentOutputValue
from app.modules.research.experiment_service import ExperimentPlanService, ExperimentPlanValidationError
from app.modules.research.experiment_visualization import preflight_test_cases


def case(x="1",y="2",group=None):
    return ExperimentTestCase(id="synthetic",inputs={"X":x,**({"B":group} if group is not None else {})},expected_outputs=[] if y is None else [ExperimentOutputValue(output_name="Y",value=y,kind="expected")])


def make_service(cases):
    plan=SimpleNamespace(test_cases=cases)
    service=object.__new__(ExperimentPlanService)
    service._get_owned_asset=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    service._plan_from_asset=Mock(return_value=plan)
    return service,plan


@pytest.mark.parametrize("kind",["radar","pie","surface",""])
def test_only_existing_experiment_renderer_kinds_allowed(kind):
    with pytest.raises(ValidationError): VisualizationSeries(name="synthetic",x=["1"],y=[2],kind=kind)
    with pytest.raises(ValidationError): VisualizationData(chart_type=kind,x_label="X",y_label="Y",series=[])


@pytest.mark.parametrize("x",["NaN","nan","Infinity","-inf","1e999"])
def test_numeric_x_must_be_finite_in_schema_and_preflight(x):
    with pytest.raises(ValidationError,match="finite"): VisualizationSeries(name="fixture",x=[x],y=[2],kind="line")
    with pytest.raises(ValueError,match="finite"): preflight_test_cases([case(x)],"X","Y",None)


@pytest.mark.parametrize("y",[float("nan"),float("inf"),float("-inf")])
def test_numeric_y_must_be_finite_in_schema_and_preflight(y):
    with pytest.raises(ValidationError): VisualizationSeries(name="fixture",x=["1"],y=[y],kind="line")
    with pytest.raises(ValueError,match="finite"): preflight_test_cases([case(y=str(y))],"X","Y",None)


def test_paired_arrays_kind_alignment_and_labels():
    with pytest.raises(ValidationError,match="equal lengths"): VisualizationSeries(name="fixture",x=["1"],y=[],kind="line")
    with pytest.raises(ValidationError): VisualizationSeries(name="x"*(budgets.MAX_LABEL+1),x=[],y=[],kind="line")
    with pytest.raises(ValidationError): VisualizationSeries(name="fixture",x=["x"*(budgets.MAX_LABEL+1)],y=[2],kind="line")
    with pytest.raises(ValidationError,match="match every series"): VisualizationData(chart_type="bar",x_label="X",y_label="Y",series=[VisualizationSeries(name="fixture",x=["1"],y=[2],kind="line")])


def test_preflight_boundaries_points_traces_total_labels(monkeypatch):
    monkeypatch.setattr(budgets,"MAX_POINTS",2)
    monkeypatch.setattr(budgets,"MAX_TRACES",2)
    monkeypatch.setattr(budgets,"MAX_TOTAL",3)
    preflight_test_cases([case("1"),case("2")],"X","Y",None)
    with pytest.raises(ValueError,match="points"): preflight_test_cases([case("1"),case("2"),case("3")],"X","Y",None)
    preflight_test_cases([case(group="A"),case(group="A"),case(group="B")],"X","Y","B")
    with pytest.raises(ValueError,match="total points"): preflight_test_cases([case(group="A")]*2+[case(group="B")]*2,"X","Y","B")
    with pytest.raises(ValueError,match="series"): preflight_test_cases([case(group="A"),case(group="B"),case(group="C")],"X","Y","B")
    preflight_test_cases([case("a"*budgets.MAX_LABEL)],"X","Y",None)
    with pytest.raises(ValueError,match="labels"): preflight_test_cases([case("a"*(budgets.MAX_LABEL+1))],"X","Y",None)


def test_schema_at_and_above_real_trace_point_budgets():
    series=VisualizationSeries(name="fixture",x=["1"]*budgets.MAX_POINTS,y=[2]*budgets.MAX_POINTS,kind="line")
    assert len(series.x)==budgets.MAX_POINTS
    with pytest.raises(ValidationError): VisualizationSeries(name="fixture",x=["1"]*(budgets.MAX_POINTS+1),y=[2]*(budgets.MAX_POINTS+1),kind="line")
    empty=VisualizationSeries(name="fixture",x=[],y=[],kind="line")
    assert VisualizationData(chart_type="line",x_label="X",y_label="Y",series=[empty]*budgets.MAX_TRACES)
    with pytest.raises(ValidationError): VisualizationData(chart_type="line",x_label="X",y_label="Y",series=[empty]*(budgets.MAX_TRACES+1))


def test_schema_exact_total_and_bytes_boundaries(monkeypatch):
    series=VisualizationSeries(name="fixture",x=["1","2"],y=[2,3],kind="line")
    raw={"chart_type":"line","x_label":"X","y_label":"Y","series":[series,series]}
    monkeypatch.setattr(budgets,"MAX_TOTAL",4)
    visual=VisualizationData(**raw)
    monkeypatch.setattr(budgets,"MAX_TOTAL",3)
    with pytest.raises(ValidationError,match="total points"): VisualizationData(**raw)
    monkeypatch.setattr(budgets,"MAX_TOTAL",4)
    size=len(json.dumps(visual.model_dump(),ensure_ascii=False,allow_nan=False).encode())
    monkeypatch.setattr(budgets,"MAX_BYTES",size)
    assert VisualizationData(**raw)
    monkeypatch.setattr(budgets,"MAX_BYTES",size-1)
    with pytest.raises(ValidationError,match="serialized bytes"): VisualizationData(**raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["points","bytes","nonfinite"])
async def test_invalid_visual_stops_before_trace_allocation_and_preserves_cases(monkeypatch,failure):
    import app.modules.research.experiment_service as module
    cases=[case("1"),case("2")]
    if failure=="points": monkeypatch.setattr(budgets,"MAX_POINTS",1)
    if failure=="bytes": monkeypatch.setattr(budgets,"MAX_BYTES",1)
    if failure=="nonfinite": cases[0]=case(y="NaN")
    service,plan=make_service(cases)
    original=[c.model_dump() for c in cases]
    allocator=Mock(side_effect=AssertionError("must not allocate traces"))
    monkeypatch.setattr(module,"prepare_input_output_series",allocator)
    with pytest.raises(ExperimentPlanValidationError): await service.get_visualization_data(uuid.uuid4(),uuid.uuid4(),"X","Y")
    allocator.assert_not_called()
    assert [c.model_dump() for c in plan.test_cases]==original


@pytest.mark.asyncio
async def test_existing_kind_policy_and_explicit_null_output_preserved():
    for cases,expected in (([case("2"),case("1")],"line"),([case("A"),case("B")],"bar"),([case("1"),case("1")],"scatter")):
        service,_=make_service(cases)
        visual=await service.get_visualization_data(uuid.uuid4(),uuid.uuid4(),"X","Y")
        assert visual.chart_type==expected
        assert all(s.kind==expected for s in visual.series)
    service,_=make_service([case("1",None),case("2","not numeric")])
    visual=await service.get_visualization_data(uuid.uuid4(),uuid.uuid4(),"X","Y")
    assert visual.series[0].y==[None,None]


def test_real_route_returns_actionable_422_and_leaves_plan_data_available():
    from app.modules.research import router as module
    service,plan=make_service([case(y="NaN")])
    app=FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[module.get_current_user]=lambda:SimpleNamespace(id=uuid.uuid4())
    app.dependency_overrides[module.get_experiment_service]=lambda:service
    with TestClient(app) as client:
        response=client.get(f"/research/experiments/{uuid.uuid4()}/visualization",params={"input_name":"X","output_name":"Y"})
    assert response.status_code==422
    assert "finite" in response.json()["detail"]
    assert plan.test_cases[0].expected_outputs[0].value=="NaN"


@pytest.mark.parametrize("values", [["1","1.0"],["0","-0"],["1e1","10"]])
def test_numerically_equivalent_x_values_use_scatter(values):
    from app.agents.planner.experiment import choose_series_kind
    assert choose_series_kind([[(value,float(index)) for index,value in enumerate(values)]]) == "scatter"


@pytest.mark.parametrize("value",["9007199254740993","-9007199254740993","9.007199254740993e15","1e-999"])
def test_original_numeric_strings_cannot_silently_lose_browser_precision(value):
    with pytest.raises(ValueError,match="precision"): preflight_test_cases([case(x=value)],"X","Y",None)
    with pytest.raises(ValueError,match="precision"): preflight_test_cases([case(y=value)],"X","Y",None)
    preflight_test_cases([case(x="9007199254740991",y="9007199254740991")],"X","Y",None)


@pytest.mark.asyncio
async def test_expected_value_provenance_and_scope_stay_aligned_after_sort():
    from app.modules.research.schemas import SourceReference
    first=case("2","4"); first.id="case-two"
    first.source_reference=SourceReference(paper_id=uuid.uuid4(),paper_title="Synthetic source",quote="Expected case two")
    second=case("1","2"); second.id="case-one"
    missing=ExperimentTestCase(id="missing-input",inputs={"Z":"1"},expected_outputs=[])
    service,plan=make_service([first,second,missing]); plan.version=7
    plan_id=uuid.uuid4()
    result=await service.get_visualization_data(uuid.uuid4(),plan_id,"X","Y")
    assert result.origin=="expected_test_case_values"
    assert result.plan_id==plan_id and result.plan_version==7
    assert result.input_name=="X" and result.output_name=="Y"
    assert result.series[0].x==["1","2"]
    assert result.series[0].test_case_ids==["case-one","case-two"]
    assert result.series[0].source_references[0] is None
    assert result.series[0].source_references[1].paper_title=="Synthetic source"
    assert (result.total_test_cases,result.plotted_test_cases,result.excluded_missing_input)==(3,2,1)
    assert result.partial is True
    assert "not observed execution" in result.scope_description


def test_legacy_metadata_stays_unknown_and_direct_unsafe_integer_rejected():
    result=VisualizationData(chart_type="line",x_label="X",y_label="Y",series=[])
    assert result.origin=="unknown" and result.plan_id is None and result.partial is None
    with pytest.raises(ValidationError,match="precision"):
        VisualizationSeries(name="fixture",x=["1"],y=[9007199254740993],kind="line")


def test_metadata_provenance_cannot_bypass_byte_preflight(monkeypatch):
    from app.modules.research.schemas import SourceReference
    c=case(); c.source_reference=SourceReference(paper_id=uuid.uuid4(),paper_title="Fixture",quote="source"*500)
    monkeypatch.setattr(budgets,"MAX_BYTES",13000)
    with pytest.raises(ValueError,match="serialized bytes"):
        preflight_test_cases([c],"X","Y",None)
