"""Runs a validated `AnalysisPlan` against a DataFrame. Every plan field
maps to one explicit pandas call: no `eval`, no `query`, no strings
turned into code."""

import math
from typing import Any, NotRequired, TypedDict

import pandas as pd

from app.agents.analytics.plan import AnalysisPlan, Filter, group_keys, output_columns

_PERIOD = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}


class AnalysisResult(TypedDict):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
    scope: NotRequired[dict[str, Any]]
    numeric_encoding: NotRequired[dict[str, str]]
    chart_withheld_reason: NotRequired[str]


def _coerce(series: pd.Series, value: Any) -> Any:
    """Compare dates as dates: an ISO string against a datetime column."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return [pd.Timestamp(item) for item in value] if isinstance(value, list) else pd.Timestamp(value)
    return value


def _mask(df: pd.DataFrame, item: Filter) -> pd.Series:
    column = df[item.column]
    value = _coerce(column, item.value)
    match item.op:
        case "eq":
            return column == value
        case "ne":
            return column != value
        case "gt":
            return column > value
        case "gte":
            return column >= value
        case "lt":
            return column < value
        case "lte":
            return column <= value
        case "in":
            return column.isin(value)
        case "not_in":
            return ~column.isin(value)
        case "between":
            return column.between(value[0], value[1])
        case "contains":
            return column.astype("string").str.contains(str(item.value), case=False, regex=False, na=False)
        case "is_null":
            return column.isna()
        case "not_null":
            return column.notna()


def execute_plan(df: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResult:
    frame = df
    for item in plan.filters:
        frame = frame[_mask(frame, item)]

    if plan.time_bucket:
        frame = frame.assign(
            **{plan.time_bucket.column: frame[plan.time_bucket.column].dt.to_period(_PERIOD[plan.time_bucket.grain]).astype(str)}
        )

    keys = group_keys(plan)
    grouped = frame.groupby(keys, dropna=True) if keys else None
    values: dict[str, Any] = {}
    for metric in plan.metrics:
        if metric.column is None:
            values[metric.alias] = grouped.size() if grouped is not None else len(frame)
        else:
            source = grouped[metric.column] if grouped is not None else frame[metric.column]
            values[metric.alias] = source.agg(metric.agg)

    result = pd.DataFrame(values).reset_index() if keys else pd.DataFrame([values])
    for metric in plan.metrics:
        if metric.as_share:
            total = result[metric.alias].sum()
            result[metric.alias] = result[metric.alias] / total * 100 if total else 0.0

    if plan.sort:
        result = result.sort_values(plan.sort.by, ascending=not plan.sort.descending, kind="stable")
    elif plan.time_bucket:
        result = result.sort_values(plan.time_bucket.column, kind="stable")

    columns = output_columns(plan)
    total_rows = len(result)
    limited = result[columns].head(plan.limit)
    # Native scalar encoding preserves Python's float precision. pandas.to_json
    # defaults to ten decimal places and silently changes computed observations.
    rows = limited.to_dict(orient="records")
    exact_integer_fields = set()
    for row in rows:
        for column, value in row.items():
            if pd.isna(value):
                row[column] = None
            elif isinstance(value, int) and not isinstance(value, bool) and abs(value) > 9007199254740991:
                row[column] = str(value)
                exact_integer_fields.add(column)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Analysis produced a non-finite result; correct the data or choose another aggregation.")
            elif isinstance(value, pd.Timestamp):
                row[column] = value.isoformat()
    output = AnalysisResult(columns=columns, rows=rows, total_rows=total_rows)
    if exact_integer_fields:
        output["numeric_encoding"] = {field: "exact_integer_string" for field in sorted(exact_integer_fields)}
        output["chart_withheld_reason"] = "Chart withheld because one or more integer results exceed exact browser numeric precision. Inspect the exact values in the result table."
    return output
