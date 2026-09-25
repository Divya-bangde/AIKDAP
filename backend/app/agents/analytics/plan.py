"""The only thing the planner LLM produces: a bounded, declarative
analysis plan, plus the validator that checks it against the real
dataset before anything runs. No expression strings, no code."""

import difflib
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.analytics.dataset import DatasetProfile

FilterOp = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "between", "contains", "is_null", "not_null"]
Aggregation = Literal["sum", "mean", "median", "min", "max", "count", "nunique"]
_NUMERIC_AGGS = {"sum", "mean", "median"}


class Filter(BaseModel):
    column: str
    op: FilterOp
    value: Any = None


class TimeBucket(BaseModel):
    column: str
    grain: Literal["day", "week", "month", "quarter", "year"]


class Metric(BaseModel):
    column: str | None = None
    agg: Aggregation
    alias: str = Field(min_length=1, max_length=64)
    as_share: bool = False


class Sort(BaseModel):
    by: str
    descending: bool = True


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "pie", "table", "kpi"]
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    series: str | None = None


class AnalysisPlan(BaseModel):
    unanswerable_reason: str | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    time_bucket: TimeBucket | None = None
    group_by: list[str] = Field(default_factory=list, max_length=2)
    metrics: list[Metric] = Field(default_factory=list, max_length=5)
    sort: Sort | None = None
    limit: int = Field(default=50, ge=1, le=1000)
    chart: ChartSpec = Field(default_factory=lambda: ChartSpec(type="table"))


def group_keys(plan: AnalysisPlan) -> list[str]:
    """Grouping columns in output order: the time bucket first (it keeps
    its column name), then `group_by`, without duplicates."""
    keys = [plan.time_bucket.column] if plan.time_bucket else []
    return keys + [column for column in plan.group_by if column not in keys]


def output_columns(plan: AnalysisPlan) -> list[str]:
    return group_keys(plan) + [metric.alias for metric in plan.metrics]


def validate_plan(plan: AnalysisPlan, profile: DatasetProfile) -> list[str]:
    """Every problem with `plan` against `profile`, as readable messages
    fit to send back to the LLM for one repair attempt."""
    if plan.unanswerable_reason:
        return []

    kinds = {column["name"]: column["kind"] for column in profile["columns"]}
    errors: list[str] = []

    def known(column: str, where: str) -> bool:
        if column in kinds:
            return True
        close = difflib.get_close_matches(column, list(kinds), n=1)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        errors.append(f"{where}: column '{column}' not found.{hint}")
        return False

    for item in plan.filters:
        known(item.column, "filter")
        if item.op == "between" and not (isinstance(item.value, list) and len(item.value) == 2):
            errors.append(f"filter on '{item.column}': 'between' needs a [low, high] list.")
        if item.op in ("in", "not_in") and not isinstance(item.value, list):
            errors.append(f"filter on '{item.column}': '{item.op}' needs a list value.")

    if plan.time_bucket and known(plan.time_bucket.column, "time_bucket"):
        if kinds[plan.time_bucket.column] != "datetime":
            errors.append(f"time_bucket: '{plan.time_bucket.column}' is not a date column.")

    for column in plan.group_by:
        known(column, "group_by")

    if not plan.metrics:
        errors.append("metrics: at least one metric is required.")
    for metric in plan.metrics:
        if metric.column is None:
            if metric.agg != "count":
                errors.append(f"metric '{metric.alias}': '{metric.agg}' needs a column.")
            continue
        if known(metric.column, f"metric '{metric.alias}'") and metric.agg in _NUMERIC_AGGS:
            if kinds[metric.column] != "numeric":
                errors.append(f"metric '{metric.alias}': '{metric.agg}' needs a numeric column; '{metric.column}' is {kinds[metric.column]}.")

    outputs = output_columns(plan)
    if len(set(outputs)) != len(outputs):
        errors.append("metric aliases must be unique and differ from grouping columns.")
    if plan.sort and plan.sort.by not in outputs:
        errors.append(f"sort: '{plan.sort.by}' is not an output column ({', '.join(outputs)}).")
    chart_fields = [plan.chart.x, plan.chart.series, *plan.chart.y]
    for field in (value for value in chart_fields if value):
        if field not in outputs:
            errors.append(f"chart: '{field}' is not an output column ({', '.join(outputs)}).")
    return errors
