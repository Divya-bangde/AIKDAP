import pytest
from pydantic import ValidationError

from app.agents.analytics.plan import AnalysisPlan, validate_plan

PROFILE = {
    "row_count": 3,
    "truncated": False,
    "columns": [
        {"name": "date", "kind": "datetime"},
        {"name": "region", "kind": "text"},
        {"name": "revenue", "kind": "numeric"},
    ],
}


def plan(**overrides) -> AnalysisPlan:
    base = {
        "group_by": ["region"],
        "metrics": [{"column": "revenue", "agg": "sum", "alias": "total_revenue"}],
        "chart": {"type": "bar", "x": "region", "y": ["total_revenue"]},
    }
    return AnalysisPlan.model_validate({**base, **overrides})


def test_valid_plan_has_no_errors():
    assert validate_plan(plan(), PROFILE) == []


def test_unknown_column_suggests_closest():
    errors = validate_plan(plan(group_by=["regoin"], chart={"type": "table", "y": ["total_revenue"]}), PROFILE)
    assert any("regoin" in error and "region" in error for error in errors)


def test_sum_on_text_column_rejected():
    errors = validate_plan(plan(metrics=[{"column": "region", "agg": "sum", "alias": "x"}], chart={"type": "table", "y": ["x"]}), PROFILE)
    assert any("numeric" in error for error in errors)


def test_time_bucket_needs_datetime():
    errors = validate_plan(plan(time_bucket={"column": "region", "grain": "month"}), PROFILE)
    assert any("date" in error for error in errors)


def test_chart_must_reference_outputs():
    errors = validate_plan(plan(chart={"type": "bar", "x": "date", "y": ["total_revenue"]}), PROFILE)
    assert any("chart" in error for error in errors)


def test_sort_must_reference_outputs():
    errors = validate_plan(plan(sort={"by": "units"}), PROFILE)
    assert any("sort" in error for error in errors)


def test_between_needs_two_values():
    errors = validate_plan(plan(filters=[{"column": "revenue", "op": "between", "value": [1]}]), PROFILE)
    assert any("between" in error for error in errors)


def test_count_without_column_allowed():
    assert validate_plan(plan(metrics=[{"agg": "count", "alias": "orders"}], chart={"type": "bar", "x": "region", "y": ["orders"]}), PROFILE) == []


def test_non_count_needs_column():
    errors = validate_plan(plan(metrics=[{"agg": "sum", "alias": "s"}], chart={"type": "table", "y": ["s"]}), PROFILE)
    assert errors


def test_bounds_enforced_by_schema():
    with pytest.raises(ValidationError):
        plan(limit=0)
    with pytest.raises(ValidationError):
        plan(group_by=["a", "b", "c"])


def test_unanswerable_plan_skips_checks():
    assert validate_plan(AnalysisPlan(unanswerable_reason="No cost column."), PROFILE) == []


def test_plan_system_prompt_carries_the_plan_schema():
    """`json_object` mode enforces JSON but not field names; without the
    schema in the prompt the LLM invents keys (e.g. a metric with no
    `column`) that Pydantic silently drops."""
    from app.agents.analytics.prompts import PLAN_SYSTEM_PROMPT

    for field in ('"column"', '"agg"', '"alias"', '"group_by"', '"metrics"'):
        assert field in PLAN_SYSTEM_PROMPT
