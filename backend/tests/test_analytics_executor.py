import pandas as pd
import pytest

from app.agents.analytics.executor import execute_plan
from app.agents.analytics.plan import AnalysisPlan

DF = pd.DataFrame(
    {
        "date": pd.to_datetime(["2024-01-05", "2024-01-20", "2024-02-03", "2024-03-15"]),
        "region": ["West", "East", "West", None],
        "revenue": [100.0, 200.0, 50.0, 25.0],
        "units": [1, 2, 3, 4],
    }
)


def run(**plan):
    plan.setdefault("metrics", [{"column": "revenue", "agg": "sum", "alias": "rev"}])
    return execute_plan(DF, AnalysisPlan.model_validate(plan))


def test_total_without_grouping():
    result = run()
    assert result["rows"] == [{"rev": 375.0}] and result["total_rows"] == 1


def test_group_by_and_sort_desc():
    result = run(group_by=["region"], sort={"by": "rev"})
    assert [row["region"] for row in result["rows"]] == ["East", "West"]


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("eq", "West", 150.0), ("ne", "West", 225.0), ("in", ["East"], 200.0),
        ("not_in", ["East"], 175.0), ("contains", "wes", 150.0),
        ("is_null", None, 25.0), ("not_null", None, 350.0),
    ],
)
def test_text_filters(op, value, expected):
    assert run(filters=[{"column": "region", "op": op, "value": value}])["rows"][0]["rev"] == expected


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [("gt", 50, 300.0), ("gte", 50, 350.0), ("lt", 100, 75.0), ("lte", 100, 175.0), ("between", [50, 100], 150.0)],
)
def test_numeric_filters(op, value, expected):
    assert run(filters=[{"column": "revenue", "op": op, "value": value}])["rows"][0]["rev"] == expected


def test_date_filter_accepts_iso_string():
    result = run(filters=[{"column": "date", "op": "gte", "value": "2024-02-01"}])
    assert result["rows"][0]["rev"] == 75.0


def test_monthly_trend_sorted_by_time():
    result = run(time_bucket={"column": "date", "grain": "month"})
    assert [row["date"] for row in result["rows"]] == ["2024-01", "2024-02", "2024-03"]
    assert [row["rev"] for row in result["rows"]] == [300.0, 50.0, 25.0]


@pytest.mark.parametrize(("agg", "expected"), [("mean", 93.75), ("median", 75.0), ("min", 25.0), ("max", 200.0), ("nunique", 4)])
def test_aggregations(agg, expected):
    assert run(metrics=[{"column": "revenue", "agg": agg, "alias": "v"}])["rows"][0]["v"] == expected


def test_count_rows():
    assert run(group_by=["region"], metrics=[{"agg": "count", "alias": "n"}], sort={"by": "n"})["rows"][0] == {"region": "West", "n": 2}


def test_share_sums_to_100():
    result = run(group_by=["region"], metrics=[{"column": "revenue", "agg": "sum", "alias": "share", "as_share": True}])
    assert sum(row["share"] for row in result["rows"]) == pytest.approx(100.0)


def test_limit_keeps_total_rows():
    result = run(group_by=["units"], limit=2)
    assert len(result["rows"]) == 2 and result["total_rows"] == 4


def test_rows_are_json_safe():
    result = run(group_by=["date"], metrics=[{"column": "units", "agg": "max", "alias": "u"}])
    assert all(isinstance(row["date"], str) and isinstance(row["u"], int) for row in result["rows"])
