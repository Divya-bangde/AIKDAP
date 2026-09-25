# Business Analytics (Milestone 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ask plain-English business questions of a project's CSV/XLSX dataset and get a computed table, a chart, and a number-checked narrative, with follow-ups and Kaggle import.

**Architecture:** A linear LangGraph (`app/agents/analytics/`) mirrors the report graph: load → plan (LLM emits a validated `AnalysisPlan`) → execute (pure pandas) → explain (LLM, non-critical) → validate (number grounding). Each analysis is a GENERATED `Asset` (type CHART) whose result lives in `asset_metadata["analysis"]`; its trace reuses `ReportStepTracker`, the `/reports/{id}/steps` endpoints, and report reconciliation. The feature module `app/modules/business_analytics/` holds the HTTP surface; `app/integrations/kaggle/` holds an httpx client.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Celery, LangGraph, pandas/numpy/openpyxl (already installed), httpx; React + TanStack Query + Plotly (already installed).

**Spec:** `docs/superpowers/specs/2026-09-25-business-analytics-design.md`

## Global Constraints

- No new dependencies (backend or frontend). No migrations.
- No generated code is executed; the LLM only produces an `AnalysisPlan`.
- `ANALYTICS_MAX_FILE_MB=50`, `ANALYTICS_MAX_ROWS=500000`; question length 1–1000 chars.
- Plan bounds: filters ≤ 10, group_by ≤ 2, metrics 1–5, limit 1–1000 (default 50).
- Kaggle credentials only via `.env` (`KAGGLE_USERNAME`, `KAGGLE_KEY`); slugs match `^[A-Za-z0-9._-]+$`.
- Routes under `/api/v1`; "not yours" == "not found" (404).
- Backend tests: `cd backend && .venv/Scripts/python -m pytest <path> -q` (real Postgres per `tests/conftest.py`). Frontend tests: `cd frontend && npx vitest run <path>`.
- After code changes run `graphify update .` if available.

---

## File Structure

Backend (create):
- `app/agents/analytics/__init__.py` – package doc.
- `app/agents/analytics/dataset.py` – `read_dataframe`, `build_profile` (pure).
- `app/agents/analytics/plan.py` – `AnalysisPlan` models + `validate_plan` (pure).
- `app/agents/analytics/executor.py` – `execute_plan` (pure).
- `app/agents/analytics/grounding.py` – `find_unverified_numbers` (pure).
- `app/agents/analytics/state.py`, `prompts.py`, `nodes.py`, `registry.py`, `graph.py` – the graph.
- `app/modules/business_analytics/{__init__,schemas,repository,service,router}.py`.
- `app/integrations/kaggle/{__init__,client}.py`.
- Tests: `tests/test_analytics_dataset.py`, `test_analytics_plan.py`, `test_analytics_executor.py`, `test_analytics_grounding.py`, `test_analytics_graph.py`, `test_analytics_routes.py`, `test_kaggle_client.py`.

Backend (modify): `app/core/config/settings.py`, `app/modules/assets/service.py` (CSV/XLSX → DATASET), `app/modules/assets/validators.py`, `app/workers/tasks.py`, `app/workers/reconciliation.py`, `app/main.py`.

Frontend (create): `src/services/analytics.ts`, `src/features/business-analytics/{analysis-chart.ts,AnalyticsWorkspace.tsx,DatasetProfile.tsx,AnalysisCard.tsx,KaggleImportDialog.tsx}` + tests.
Frontend (modify): `src/pages/ProjectDetail.tsx`, `src/types/api.d.ts` (regenerated).

---

### Task 1: Settings + dataset loading and profiling

**Files:**
- Modify: `backend/app/core/config/settings.py` (after `synthesis_model`)
- Create: `backend/app/agents/analytics/__init__.py`, `backend/app/agents/analytics/dataset.py`
- Test: `backend/tests/test_analytics_dataset.py`

**Interfaces — Produces:**
- `settings.analytics_max_file_mb: int`, `settings.analytics_max_rows: int`, `settings.kaggle_username: str | None`, `settings.kaggle_key: SecretStr | None`
- `read_dataframe(content: bytes, extension: str, *, sheet: str | None, max_rows: int) -> tuple[pd.DataFrame, bool]`
- `build_profile(df: pd.DataFrame, *, truncated: bool) -> DatasetProfile` (TypedDict: `row_count`, `truncated`, `columns: list[ColumnProfile]`; ColumnProfile: `name`, `kind` in `numeric|datetime|text|boolean`, `dtype`, `null_pct`, `distinct`, `min`, `max`, `samples`)
- `DatasetReadError(ValueError)`

- [ ] **Step 1: Add settings**

```python
    # ------------------------------------------------------------------
    # Business analytics (Milestone 6)
    # ------------------------------------------------------------------
    analytics_max_file_mb: int = Field(default=50, gt=0)
    analytics_max_rows: int = Field(default=500_000, gt=0)
    kaggle_username: str | None = None
    kaggle_key: SecretStr | None = None
    kaggle_timeout: float = Field(default=60.0, gt=0)

    @field_validator("kaggle_username", "kaggle_key", mode="before")
    @classmethod
    def blank_kaggle_credential_is_unset(cls, value: object) -> object:
        """A bare `KAGGLE_KEY=` means "not configured" (same rule as Tavily/OpenAlex)."""
        if isinstance(value, str) and not value.strip():
            return None
        return value
```

- [ ] **Step 2: Write failing tests** (`tests/test_analytics_dataset.py`)

```python
import io

import pandas as pd
import pytest

from app.agents.analytics.dataset import DatasetReadError, build_profile, read_dataframe

CSV = b"date,region,revenue,units\n2024-01-05,West,100.5,3\n2024-02-10,East,200,5\n2024-02-11,West,,2\n"


def test_reads_csv_and_parses_dates():
    df, truncated = read_dataframe(CSV, ".csv", sheet=None, max_rows=100)
    assert not truncated
    assert list(df.columns) == ["date", "region", "revenue", "units"]
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_row_cap_truncates():
    df, truncated = read_dataframe(CSV, ".csv", sheet=None, max_rows=2)
    assert len(df) == 2 and truncated


def test_reads_xlsx_named_sheet():
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer) as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"b": [2, 3]}).to_excel(writer, sheet_name="second", index=False)
    df, _ = read_dataframe(buffer.getvalue(), ".xlsx", sheet="second", max_rows=10)
    assert list(df.columns) == ["b"] and len(df) == 2


def test_unreadable_file_raises():
    with pytest.raises(DatasetReadError):
        read_dataframe(b"\x00\x01garbage", ".xlsx", sheet=None, max_rows=10)


def test_unsupported_extension_raises():
    with pytest.raises(DatasetReadError):
        read_dataframe(CSV, ".pdf", sheet=None, max_rows=10)


def test_profile_describes_columns():
    df, truncated = read_dataframe(CSV, ".csv", sheet=None, max_rows=100)
    profile = build_profile(df, truncated=truncated)
    assert profile["row_count"] == 3
    columns = {column["name"]: column for column in profile["columns"]}
    assert columns["revenue"]["kind"] == "numeric"
    assert columns["revenue"]["null_pct"] == pytest.approx(33.3, abs=0.1)
    assert columns["date"]["kind"] == "datetime"
    assert columns["date"]["min"] == "2024-01-05T00:00:00"
    assert columns["region"]["kind"] == "text"
    assert columns["region"]["distinct"] == 2
    assert len(columns["region"]["samples"]) <= 5
```

- [ ] **Step 3: Run to verify failure** — `.venv/Scripts/python -m pytest tests/test_analytics_dataset.py -q` → ModuleNotFoundError.

- [ ] **Step 4: Implement** `app/agents/analytics/__init__.py`:

```python
"""Business-analytics LangGraph workflow (Milestone 6).

Orchestration and pure data logic only: dataset reading/profiling, the
`AnalysisPlan` contract and its validator, the pandas executor, and the
number-grounding check. Persistence and HTTP live in
`app.modules.business_analytics`; the dependency direction is one-way.
"""
```

`app/agents/analytics/dataset.py`:

```python
"""Reading a stored CSV/XLSX into pandas and describing it.

The planner LLM only ever sees `build_profile`'s output, never raw rows.
"""

import io
import json
from typing import Any, Literal, TypedDict

import pandas as pd

# ponytail: whole dataset loaded in memory in the worker, capped by
# `analytics_max_rows`; move to DuckDB/chunked reads if datasets outgrow it.

#: A text column becomes datetime when at least this share of its
#: non-null values parse as dates.
_DATE_PARSE_THRESHOLD = 0.9
_SAMPLE_SIZE = 5

ColumnKind = Literal["numeric", "datetime", "text", "boolean"]


class ColumnProfile(TypedDict):
    name: str
    kind: ColumnKind
    dtype: str
    null_pct: float
    distinct: int
    min: Any
    max: Any
    samples: list[Any]


class DatasetProfile(TypedDict):
    row_count: int
    truncated: bool
    columns: list[ColumnProfile]


class DatasetReadError(ValueError):
    """The file could not be read as a dataset."""


def read_dataframe(
    content: bytes, extension: str, *, sheet: str | None, max_rows: int
) -> tuple[pd.DataFrame, bool]:
    """Parse CSV/XLSX bytes, reading at most `max_rows` rows.

    Returns the frame and whether it was truncated. Text columns that
    are overwhelmingly dates are converted to datetime so they can be
    bucketed by time.
    """
    try:
        if extension == ".csv":
            df = pd.read_csv(io.BytesIO(content), nrows=max_rows + 1)
        elif extension in (".xlsx", ".xls"):
            df = pd.read_excel(io.BytesIO(content), sheet_name=sheet or 0, nrows=max_rows + 1)
        else:
            raise DatasetReadError(f"'{extension}' files are not datasets; use CSV or XLSX.")
    except DatasetReadError:
        raise
    except Exception as exc:  # pandas raises many parser-specific types
        raise DatasetReadError(f"The file could not be read ({type(exc).__name__}).") from exc

    truncated = len(df) > max_rows
    df = df.head(max_rows)
    df.columns = [str(column) for column in df.columns]
    for column in df.columns:
        if df[column].dtype == object:
            df[column] = _maybe_dates(df[column])
    return df, truncated


def _maybe_dates(series: pd.Series) -> pd.Series:
    values = series.dropna()
    if values.empty or not all(isinstance(value, str) for value in values.head(50)):
        return series
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    if parsed.notna().sum() >= _DATE_PARSE_THRESHOLD * len(values):
        return parsed
    return series


def column_kind(series: pd.Series) -> ColumnKind:
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    return "text"


def _json_safe(values: list[Any]) -> list[Any]:
    """numpy/pandas scalars -> plain JSON values (NaN -> None, dates -> ISO)."""
    return json.loads(pd.Series(values, dtype=object).to_json(orient="values", date_format="iso"))


def build_profile(df: pd.DataFrame, *, truncated: bool) -> DatasetProfile:
    columns: list[ColumnProfile] = []
    for name in df.columns:
        series = df[name]
        kind = column_kind(series)
        non_null = series.dropna()
        low, high = (
            _json_safe([non_null.min(), non_null.max()])
            if kind in ("numeric", "datetime") and not non_null.empty
            else [None, None]
        )
        columns.append(
            ColumnProfile(
                name=name,
                kind=kind,
                dtype=str(series.dtype),
                null_pct=round(float(series.isna().mean() * 100), 1) if len(series) else 0.0,
                distinct=int(series.nunique(dropna=True)),
                min=low,
                max=high,
                samples=_json_safe(list(non_null.drop_duplicates().head(_SAMPLE_SIZE))),
            )
        )
    return DatasetProfile(row_count=len(df), truncated=truncated, columns=columns)
```

Note `date_format="iso"` emits `2024-01-05T00:00:00.000`; adjust the test's expected `min` to whatever `to_json` emits if it differs (`"2024-01-05T00:00:00.000"`), keeping one representation everywhere.

- [ ] **Step 5: Run tests** — expect PASS.
- [ ] **Step 6: Commit** — `git add backend/app/core/config/settings.py backend/app/agents/analytics backend/tests/test_analytics_dataset.py && git commit -m "feat(analytics): dataset loading and profiling"`

---

### Task 2: AnalysisPlan contract and validator

**Files:** Create `backend/app/agents/analytics/plan.py`; Test `backend/tests/test_analytics_plan.py`

**Interfaces:**
- Consumes: `DatasetProfile`, `ColumnKind` (Task 1)
- Produces: `Filter`, `TimeBucket`, `Metric`, `Sort`, `ChartSpec`, `AnalysisPlan` (Pydantic), `validate_plan(plan: AnalysisPlan, profile: DatasetProfile) -> list[str]`, `output_columns(plan) -> list[str]`, `group_keys(plan) -> list[str]`

- [ ] **Step 1: Failing tests**

```python
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
```

- [ ] **Step 2: Run** → fails (module missing).
- [ ] **Step 3: Implement** `plan.py`:

```python
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
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(analytics): AnalysisPlan contract and validator`.

---

### Task 3: pandas executor

**Files:** Create `backend/app/agents/analytics/executor.py`; Test `backend/tests/test_analytics_executor.py`

**Interfaces:**
- Consumes: `AnalysisPlan`, `group_keys`, `output_columns` (Task 2)
- Produces: `execute_plan(df: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResult` where `AnalysisResult` is a TypedDict `{columns: list[str], rows: list[dict[str, Any]], total_rows: int}`

- [ ] **Step 1: Failing tests** (table-driven around one fixture frame)

```python
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
    [("gt", 50, 325.0), ("gte", 50, 350.0), ("lt", 100, 75.0), ("lte", 100, 175.0), ("between", [50, 100], 150.0)],
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
```

- [ ] **Step 2: Run** → fails.
- [ ] **Step 3: Implement** `executor.py`:

```python
"""Runs a validated `AnalysisPlan` against a DataFrame. Every plan field
maps to one explicit pandas call: no `eval`, no `query`, no strings
turned into code."""

import json
from typing import Any, TypedDict

import pandas as pd

from app.agents.analytics.plan import AnalysisPlan, Filter, group_keys, output_columns

_PERIOD = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}


class AnalysisResult(TypedDict):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int


def _coerce(series: pd.Series, value: Any) -> Any:
    """Compare dates as dates: an ISO string against a datetime column."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return [pd.Timestamp(item) for item in value] if isinstance(value, list) else pd.Timestamp(value)
    return value


def _mask(df: pd.DataFrame, item: Filter) -> pd.Series:
    column = df[item.column]
    value = _coerce(column, item.value)
    match item.op:
        case "eq": return column == value
        case "ne": return column != value
        case "gt": return column > value
        case "gte": return column >= value
        case "lt": return column < value
        case "lte": return column <= value
        case "in": return column.isin(value)
        case "not_in": return ~column.isin(value)
        case "between": return column.between(value[0], value[1])
        case "contains": return column.astype("string").str.contains(str(item.value), case=False, regex=False, na=False)
        case "is_null": return column.isna()
        case "not_null": return column.notna()


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
    rows = json.loads(limited.to_json(orient="records", date_format="iso"))
    return AnalysisResult(columns=columns, rows=rows, total_rows=total_rows)
```

(`groupby(...).size()` on a single key returns a Series named by the key; `pd.DataFrame(values)` aligns every metric on the group index, then `reset_index` restores the key columns.)

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(analytics): pandas plan executor`.

---

### Task 4: Number grounding

**Files:** Create `backend/app/agents/analytics/grounding.py`; Test `backend/tests/test_analytics_grounding.py`

**Interfaces — Produces:** `find_unverified_numbers(narrative: str, rows: list[dict[str, Any]]) -> list[str]` (returns the narrative substrings, in order, de-duplicated)

- [ ] **Step 1: Failing tests**

```python
from app.agents.analytics.grounding import find_unverified_numbers

ROWS = [{"region": "West", "rev": 1234567.891, "share": 61.73}, {"region": "East", "rev": 765432.1, "share": 38.27}]


def test_matching_numbers_pass():
    text = "West earned 1,234,567.89 (61.7%) versus East's 765,432."
    assert find_unverified_numbers(text, ROWS) == []


def test_abbreviated_numbers_pass():
    assert find_unverified_numbers("West brought in $1.23M.", ROWS) == []


def test_invented_number_flagged():
    assert find_unverified_numbers("West earned 9,999 and grew 12%.", ROWS) == ["9,999", "12%"]


def test_small_counting_words_ignored():
    assert find_unverified_numbers("The top 3 regions are listed.", ROWS) == []


def test_numbers_inside_labels_count():
    rows = [{"date": "2024-01", "rev": 5.0}]
    assert find_unverified_numbers("In 2024-01 revenue was 5.", rows) == []
```

- [ ] **Step 2: Run** → fails.
- [ ] **Step 3: Implement**

```python
"""Checks every number the narrative states against the result table.

The explain LLM sees only the result rows, so any number it writes that
is not (within rounding) one of those values is flagged to the user."""

import re
from typing import Any

_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?\s?(?:[kKmMbB](?![a-zA-Z])|%)?")
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}
_TOLERANCE = 0.01  # 1% relative: "1.23M" matches 1,234,567.89

# ponytail: integers 0-10 are skipped as counting words ("top 3"); a
# narrative stating a genuinely wrong small number goes unflagged.
_SMALL_INT_MAX = 10


def _parse(token: str) -> float:
    cleaned = token.replace(",", "").replace(" ", "").rstrip("%")
    scale = _SCALE.get(cleaned[-1].lower(), 1.0) if cleaned[-1].isalpha() else 1.0
    return float(cleaned.rstrip("kKmMbB")) * scale


def _known_values(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        for value in row.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                values.append(float(value))
            else:
                values.extend(float(match) for match in re.findall(r"\d+(?:\.\d+)?", str(value)))
    return values


def _matches(number: float, known: list[float]) -> bool:
    return any(abs(number - value) <= max(abs(value) * _TOLERANCE, 0.005) for value in known)


def find_unverified_numbers(narrative: str, rows: list[dict[str, Any]]) -> list[str]:
    known = _known_values(rows)
    flagged: list[str] = []
    for match in _NUMBER.finditer(narrative):
        token = match.group(0).strip()
        number = _parse(token)
        if number.is_integer() and 0 <= number <= _SMALL_INT_MAX and not token.endswith("%"):
            continue
        if not _matches(number, known) and token not in flagged:
            flagged.append(token)
    return flagged
```

Label parts like `2024-01` produce the tokens `2024` and `-01`→ handled because `_known_values` extracts `2024` and `01` (=1.0) from the string cell; `-01` parses to -1 → small int? It is negative, so add `abs()`: use `0 <= abs(number) <= _SMALL_INT_MAX`. Update the condition accordingly while implementing.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(analytics): narrative number grounding check`.

---

### Task 5: Analytics graph (state, prompts, nodes, registry, graph)

**Files:** Create `backend/app/agents/analytics/{state,prompts,nodes,registry,graph}.py`; Test `backend/tests/test_analytics_graph.py`

**Interfaces:**
- Consumes: Tasks 1–4; `NodeSpec` (`app.agents.planner.registry`), `instrument` + `TRACKER_CONFIG_KEY` (`app.agents.planner.tracking`), `LLMGateway.generate(prompt=, system_prompt=, model=, response_format=)` → `LLMResponse.content`, `StorageProvider.read(path) -> bytes`.
- Produces:
  - `AnalyticsNode` enum: `LOAD_DATASET="load_dataset"`, `PLAN_ANALYSIS="plan_analysis"`, `EXECUTE="execute"`, `EXPLAIN="explain"`, `VALIDATE="validate"`
  - `AnalyticsState` TypedDict inputs: `analysis_id, question, storage_path, file_extension, sheet, history: list[HistoryItem]`; outputs: `profile, frame (DataFrame, in-memory only), plan (dict), result (AnalysisResult | None), narrative (str | None), unverified_numbers (list[str]), intermediate_results (dict), step`
  - `HistoryItem` TypedDict `{question: str, plan: dict}`
  - `AnalyticsGraphDependencies(storage: StorageProvider, llm_gateway: LLMGateway)`; `build_analytics_dependencies() -> AnalyticsGraphDependencies`
  - `ANALYTICS_AGENT_REGISTRY: dict[str, NodeSpec]`; `get_analytics_graph() -> CompiledStateGraph`
  - `AnalysisPlanError(ValueError)` raised when a plan is still invalid after one repair.

- [ ] **Step 1: Failing graph tests**

```python
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


async def test_happy_path():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East leads with 300, West has 150.")
    state = await run(gateway)
    assert state["result"]["rows"] == [{"region": "East", "total": 300}, {"region": "West", "total": 150}]
    assert state["narrative"].startswith("East leads")
    assert state["unverified_numbers"] == []
    assert state["profile"]["row_count"] == 3


async def test_invalid_plan_repaired_once():
    bad = {**GOOD_PLAN, "group_by": ["regoin"]}
    gateway = ScriptedGateway(json.dumps(bad), json.dumps(GOOD_PLAN), "East 300.")
    state = await run(gateway)
    assert state["result"]["total_rows"] == 2
    assert "regoin" in gateway.calls[1]["prompt"]


async def test_plan_still_invalid_fails_run():
    bad = json.dumps({**GOOD_PLAN, "group_by": ["regoin"]})
    with pytest.raises(AnalysisPlanError):
        await run(ScriptedGateway(bad, bad))


async def test_unanswerable_completes_without_result_or_llm_narrative():
    gateway = ScriptedGateway(json.dumps({"unanswerable_reason": "There is no cost column."}))
    state = await run(gateway)
    assert state["result"] is None
    assert state["narrative"] == "There is no cost column."
    assert len(gateway.calls) == 1


async def test_explain_failure_is_not_fatal():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), RuntimeError("provider down"))
    state = await run(gateway)
    assert state["result"]["total_rows"] == 2
    assert state.get("narrative") is None
    assert "explain_failure" in state["intermediate_results"]


async def test_invented_number_flagged():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East made 999.")
    assert (await run(gateway))["unverified_numbers"] == ["999"]


async def test_history_reaches_planner_prompt():
    gateway = ScriptedGateway(json.dumps(GOOD_PLAN), "East 300.")
    await run(gateway, history=[{"question": "Total revenue?", "plan": {"metrics": []}}])
    assert "Total revenue?" in gateway.calls[0]["prompt"]
```

(`pytest.ini` already sets asyncio mode; confirm `asyncio_mode = auto` there, otherwise add `@pytest.mark.asyncio` as other graph tests do.)

- [ ] **Step 2: Run** → fails.

- [ ] **Step 3: Implement `state.py`**

```python
"""Shared state for the analytics graph. Linear graph, no reducers.

`frame` is the one non-JSON key: the loaded DataFrame, passed from
`load_dataset` to `execute` in memory. Safe because the graph is
compiled without a checkpointer, and it is never persisted."""

import enum
from typing import Any, TypedDict

import pandas as pd

from app.agents.analytics.dataset import DatasetProfile
from app.agents.analytics.executor import AnalysisResult


class AnalyticsNode(str, enum.Enum):
    LOAD_DATASET = "load_dataset"
    PLAN_ANALYSIS = "plan_analysis"
    EXECUTE = "execute"
    EXPLAIN = "explain"
    VALIDATE = "validate"


class HistoryItem(TypedDict):
    question: str
    plan: dict[str, Any]


class AnalyticsState(TypedDict, total=False):
    # Inputs
    analysis_id: str
    question: str
    storage_path: str
    file_extension: str
    sheet: str | None
    history: list[HistoryItem]
    # Outputs
    profile: DatasetProfile
    frame: pd.DataFrame
    plan: dict[str, Any]
    result: AnalysisResult | None
    narrative: str | None
    unverified_numbers: list[str]
    intermediate_results: dict[str, Any]
    step: dict[str, Any]
```

- [ ] **Step 4: Implement `prompts.py`**

```python
"""Prompts for the analytics graph."""

import json
from typing import Any

from app.agents.analytics.dataset import DatasetProfile
from app.agents.analytics.state import HistoryItem

PLAN_SYSTEM_PROMPT = """You translate a business question about ONE tabular dataset into a JSON analysis plan.
Rules:
- Use only column names from the dataset profile, spelled exactly.
- sum/mean/median only on numeric columns. count may omit "column" to count rows.
- time_bucket only on datetime columns; the bucketed column keeps its name.
- Output columns are: the time_bucket column, then group_by columns, then metric aliases.
  sort.by and every chart field must be output columns.
- Chart: "line" for trends over time, "bar" for comparing categories, "pie" for shares
  (use as_share), "kpi" for a single number, "table" otherwise.
- If the dataset cannot answer the question, return only {"unanswerable_reason": "<why, one sentence>"}.
Return JSON only."""

EXPLAIN_SYSTEM_PROMPT = """You explain the result of a data analysis to a business user in at most 120 words.
Use ONLY numbers that appear in the result table; round sensibly. Lead with the direct answer.
Mention if the result is truncated. Plain text, no markdown headings."""


def render_plan_prompt(*, question: str, profile: DatasetProfile, history: list[HistoryItem], errors: list[str] | None = None) -> str:
    parts = [f"Dataset profile:\n{json.dumps(profile, default=str)}"]
    if history:
        earlier = "\n".join(f"- Q: {item['question']}\n  plan: {json.dumps(item['plan'])}" for item in history)
        parts.append(f"Earlier questions on this dataset (the new question may refine them):\n{earlier}")
    parts.append(f"Question: {question}")
    if errors:
        parts.append("Your previous plan was invalid. Fix these problems:\n" + "\n".join(f"- {error}" for error in errors))
    return "\n\n".join(parts)


def render_explain_prompt(*, question: str, result: dict[str, Any]) -> str:
    shown = result["rows"][:50]
    note = f" (showing {len(shown)} of {result['total_rows']} rows)" if result["total_rows"] > len(shown) else ""
    return f"Question: {question}\n\nResult table{note}:\n{json.dumps(shown)}"
```

- [ ] **Step 5: Implement `nodes.py`**

```python
"""Node handlers for the analytics graph."""

import re
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from app.agents.analytics.dataset import build_profile, read_dataframe
from app.agents.analytics.executor import execute_plan
from app.agents.analytics.grounding import find_unverified_numbers
from app.agents.analytics.plan import AnalysisPlan, validate_plan
from app.agents.analytics.prompts import (
    EXPLAIN_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    render_explain_prompt,
    render_plan_prompt,
)
from app.agents.analytics.state import AnalyticsState
from app.core.config.settings import settings
from app.core.llm.gateway import LLMGateway, get_llm_gateway
from app.modules.assets.storage import StorageProvider, get_storage_provider

_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class AnalysisPlanError(ValueError):
    """The planner could not produce a valid plan after one repair."""


@dataclass(frozen=True)
class AnalyticsGraphDependencies:
    storage: StorageProvider
    llm_gateway: LLMGateway


def build_analytics_dependencies() -> AnalyticsGraphDependencies:
    return AnalyticsGraphDependencies(storage=get_storage_provider(), llm_gateway=get_llm_gateway())


def _dependencies(config: RunnableConfig) -> AnalyticsGraphDependencies:
    dependencies = (config.get("configurable") or {}).get("dependencies")
    if not isinstance(dependencies, AnalyticsGraphDependencies):
        raise RuntimeError("Analytics graph invoked without AnalyticsGraphDependencies.")
    return dependencies


async def load_dataset_node(state: AnalyticsState, config: RunnableConfig) -> dict[str, Any]:
    content = await _dependencies(config).storage.read(state["storage_path"])
    frame, truncated = read_dataframe(
        content, state["file_extension"], sheet=state.get("sheet"), max_rows=settings.analytics_max_rows
    )
    profile = build_profile(frame, truncated=truncated)
    note = " (truncated)" if truncated else ""
    return {
        "frame": frame,
        "profile": profile,
        "step": {
            "summary": f"Loaded {profile['row_count']} rows and {len(profile['columns'])} columns{note}.",
            "output": {"row_count": profile["row_count"], "column_count": len(profile["columns"]), "truncated": truncated},
        },
    }


def _parse_plan(content: str) -> tuple[AnalysisPlan | None, list[str]]:
    match = _JSON_FENCE.match(content)
    try:
        return AnalysisPlan.model_validate_json(match.group(1) if match else content), []
    except ValidationError as exc:
        return None, [f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()]


async def plan_analysis_node(state: AnalyticsState, config: RunnableConfig) -> dict[str, Any]:
    gateway = _dependencies(config).llm_gateway
    errors: list[str] | None = None
    for attempt in (1, 2):
        response = await gateway.generate(
            prompt=render_plan_prompt(
                question=state["question"], profile=state["profile"], history=state.get("history", []), errors=errors
            ),
            system_prompt=PLAN_SYSTEM_PROMPT,
            model=settings.planner_model,
            response_format={"type": "json_object"},
        )
        plan, errors = _parse_plan(response.content)
        if plan is not None:
            errors = validate_plan(plan, state["profile"])
        if not errors:
            summary = (
                f"Not answerable from this dataset: {plan.unanswerable_reason}"
                if plan.unanswerable_reason
                else f"Planned {len(plan.metrics)} metric(s) over {len(plan.group_by) + bool(plan.time_bucket)} grouping(s)."
            )
            return {"plan": plan.model_dump(mode="json"), "step": {"summary": summary, "output": {"attempts": attempt}}}
    raise AnalysisPlanError("Could not build a valid analysis plan: " + "; ".join(errors))


async def execute_node(state: AnalyticsState, config: RunnableConfig) -> dict[str, Any]:
    plan = AnalysisPlan.model_validate(state["plan"])
    if plan.unanswerable_reason:
        return {"result": None, "step": {"summary": "Skipped: the question is not answerable from this dataset."}}
    result = execute_plan(state["frame"], plan)
    return {
        "result": result,
        "step": {
            "summary": f"Computed {result['total_rows']} result row(s).",
            "output": {"total_rows": result["total_rows"], "returned_rows": len(result["rows"])},
        },
    }


async def explain_node(state: AnalyticsState, config: RunnableConfig) -> dict[str, Any]:
    reason = state["plan"].get("unanswerable_reason")
    if reason:
        return {"narrative": reason, "step": {"summary": "Explained why the question cannot be answered."}}
    response = await _dependencies(config).llm_gateway.generate(
        prompt=render_explain_prompt(question=state["question"], result=state["result"]),
        system_prompt=EXPLAIN_SYSTEM_PROMPT,
        model=settings.synthesis_model,
    )
    narrative = response.content.strip()
    return {"narrative": narrative, "step": {"summary": f"Wrote a {len(narrative.split())}-word explanation."}}


async def validate_node(state: AnalyticsState, config: RunnableConfig) -> dict[str, Any]:
    narrative = state.get("narrative")
    result = state.get("result")
    if not narrative or result is None:
        return {"unverified_numbers": [], "step": {"summary": "Nothing to check."}}
    unverified = find_unverified_numbers(narrative, result["rows"])
    summary = (
        "Every number in the explanation matches the result."
        if not unverified
        else f"{len(unverified)} number(s) in the explanation are not in the result."
    )
    return {"unverified_numbers": unverified, "step": {"summary": summary, "output": {"unverified": unverified}}}
```

- [ ] **Step 6: Implement `registry.py` and `graph.py`**

```python
"""Registry of the analytics graph's nodes (mirrors `agents.reports.registry`).
Only `explain` is non-critical: without a narrative the computed table
and chart are still a complete answer."""

from app.agents.analytics.nodes import (
    execute_node,
    explain_node,
    load_dataset_node,
    plan_analysis_node,
    validate_node,
)
from app.agents.analytics.state import AnalyticsNode
from app.agents.planner.registry import NodeSpec

ANALYTICS_AGENT_REGISTRY: dict[str, NodeSpec] = {
    AnalyticsNode.LOAD_DATASET.value: NodeSpec(
        name=AnalyticsNode.LOAD_DATASET.value, title="Load dataset", handler=load_dataset_node,
        critical=True, description="Reads the dataset and profiles its columns.",
    ),
    AnalyticsNode.PLAN_ANALYSIS.value: NodeSpec(
        name=AnalyticsNode.PLAN_ANALYSIS.value, title="Plan analysis", handler=plan_analysis_node,
        critical=True, description="Turns the question into a validated analysis plan.",
    ),
    AnalyticsNode.EXECUTE.value: NodeSpec(
        name=AnalyticsNode.EXECUTE.value, title="Compute result", handler=execute_node,
        critical=True, description="Runs the plan over the dataset.",
    ),
    AnalyticsNode.EXPLAIN.value: NodeSpec(
        name=AnalyticsNode.EXPLAIN.value, title="Explain result", handler=explain_node,
        critical=False, description="Writes a short explanation from the result only.",
    ),
    AnalyticsNode.VALIDATE.value: NodeSpec(
        name=AnalyticsNode.VALIDATE.value, title="Check numbers", handler=validate_node,
        critical=True, description="Checks every number in the explanation against the result.",
    ),
}
```

```python
"""The analytics LangGraph orchestrator: strictly linear, like the report graph."""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.analytics.registry import ANALYTICS_AGENT_REGISTRY
from app.agents.analytics.state import AnalyticsState
from app.agents.planner.tracking import instrument


def build_analytics_graph() -> StateGraph:
    builder: StateGraph = StateGraph(AnalyticsState)
    order = list(ANALYTICS_AGENT_REGISTRY)
    for name in order:
        builder.add_node(name, instrument(ANALYTICS_AGENT_REGISTRY[name]))
    for source, target in zip([START, *order], [*order, END]):
        builder.add_edge(source, target)
    return builder


@lru_cache(maxsize=1)
def get_analytics_graph() -> CompiledStateGraph:
    return build_analytics_graph().compile()
```

`instrument` calls `get_tracker(config)`; check that it defaults to a no-op tracker when none is configured (the report graph tests run without one). If it does not, pass a no-op tracker in the test config the same way `tests/test_reports_graph.py` does.

- [ ] **Step 7: Run** `pytest tests/test_analytics_graph.py -q` → PASS. **Step 8: Commit** `feat(analytics): LangGraph analytics workflow`.

---

### Task 6: Worker task, reconciliation, and DATASET classification

**Files:**
- Modify: `backend/app/workers/tasks.py` (append after `_generate_report`), `backend/app/workers/reconciliation.py` (`_REPORT_ASSET_TYPES`), `backend/app/modules/assets/validators.py` + `backend/app/modules/assets/service.py:153-154`
- Test: extend `backend/tests/test_analytics_graph.py` is not enough → add `backend/tests/test_analytics_worker.py`

**Interfaces:**
- Consumes: `get_analytics_graph`, `build_analytics_dependencies`, `ANALYTICS_AGENT_REGISTRY`, `ReportRepository.claim_pending`, `ReportStepTracker(session, asset_id, attempt=, registry=, session_factory=)`, `scrub_report_error`
- Produces: Celery task `workers.run_analysis(asset_id: str)`; async `_run_analysis(asset_id: uuid.UUID) -> dict[str, str]`. Analysis asset metadata contract (written by Task 7, completed here):

```python
asset_metadata["analysis"] = {
    "dataset_id": str, "question": str, "sheet": str | None,
    "plan": dict | None, "result": AnalysisResult | None,
    "narrative": str | None, "unverified_numbers": list[str],
}
```

- [ ] **Step 1: DATASET classification.** In `validators.py` add below `DOCUMENT_EXTENSIONS`:

```python
#: Tabular formats business analytics can query; an upload of one with no
#: explicit type is a DATASET.
DATASET_EXTENSIONS: frozenset[str] = frozenset({".csv", ".xlsx", ".xls"})
```

In `service.py` import it and change the inference to:

```python
            asset_type=asset_type
            or (
                AssetType.DOCUMENT
                if extension in DOCUMENT_EXTENSIONS
                else AssetType.DATASET
                if extension in DATASET_EXTENSIONS
                else AssetType.OTHER
            ),
```

Add a test to `tests/test_asset_service.py` next to the existing extension-inference test (find it with `grep -n "DOCUMENT_EXTENSIONS\|AssetType.OTHER" tests/test_asset_service.py`) asserting a `.csv` upload with no type becomes `AssetType.DATASET`.

- [ ] **Step 2: Reconciliation.** In `reconciliation.py` change:

```python
#: Asset types a GENERATED report-style job creates: synopsis/summary
#: reports and business-analytics analyses (CHART). All run through the
#: same pending -> running -> terminal lifecycle.
_REPORT_ASSET_TYPES = (AssetType.REPORT, AssetType.SUMMARY, AssetType.CHART)
```

Extend `tests/test_report_generation_reconciliation.py` with one case: a stale GENERATED CHART asset at `running` becomes `failed`.

- [ ] **Step 3: Failing worker test** (`tests/test_analytics_worker.py`) — builds a real dataset asset (stored via `LocalStorageProvider` into a tmp dir by monkeypatching `app.agents.analytics.nodes.get_storage_provider`) and a pending analysis asset, monkeypatches `app.agents.analytics.nodes.get_llm_gateway` with `ScriptedGateway` (copy from Task 5), runs `_run_analysis`, and asserts: status COMPLETED, `asset_metadata["analysis"]["result"]["rows"]` populated, the dataset asset's `asset_metadata["profile"]` cached, five `research_steps` rows. A second test with an invalid plan twice asserts FAILED, `processing_error == scrub_report_error(...)` text, and remaining steps `skipped`. Use the `project` fixture from `tests/conftest.py` for ownership.

- [ ] **Step 4: Implement in `workers/tasks.py`** (imports at top alongside the report imports):

```python
from app.agents.analytics.graph import get_analytics_graph
from app.agents.analytics.nodes import build_analytics_dependencies
from app.agents.analytics.registry import ANALYTICS_AGENT_REGISTRY
```

```python
# ---------------------------------------------------------------------------
# Milestone 6: business analytics
# ---------------------------------------------------------------------------


@celery_app.task(name="workers.run_analysis", bind=True, max_retries=0)
@log_task_execution
def run_analysis(self, asset_id: str) -> dict[str, str]:
    """Answer one analysis question over its dataset. Same lifecycle as
    `generate_report`: claim, trace, terminal status on the asset."""
    return _run_task_loop(_run_analysis(uuid.UUID(asset_id)))


async def _run_analysis(asset_id: uuid.UUID) -> dict[str, str]:
    async with async_session_factory() as session:
        assets = AssetRepository(session)
        claimed = await ReportRepository(session).claim_pending(asset_id)
        await session.commit()
        if not claimed:
            return {"status": "skipped", "asset_id": str(asset_id)}

        asset = await assets.get_by_id(asset_id)
        analysis = dict(asset.asset_metadata.get("analysis") or {})
        dataset = await assets.get_by_id(uuid.UUID(analysis["dataset_id"]))
        tracker: ReportStepTracker | None = None
        try:
            if dataset is None:
                raise LookupError("The dataset no longer exists.")
            attempt = await ResearchStepRepository(session).latest_attempt(asset_id) + 1
            tracker = ReportStepTracker(
                session, asset_id, attempt=attempt,
                registry=ANALYTICS_AGENT_REGISTRY, session_factory=own_session_factory(),
            )
            history = [
                {"question": item["question"], "plan": item["plan"]}
                for item in analysis.get("history", [])
            ]
            state = await get_analytics_graph().ainvoke(
                {
                    "analysis_id": str(asset_id),
                    "question": analysis["question"],
                    "storage_path": dataset.storage_path,
                    "file_extension": f".{dataset.file_extension.lstrip('.')}",
                    "sheet": analysis.get("sheet"),
                    "history": history,
                },
                config={"configurable": {"dependencies": build_analytics_dependencies(), TRACKER_CONFIG_KEY: tracker}},
            )
            dataset.asset_metadata = {**dataset.asset_metadata, "profile": state["profile"]}
            asset.asset_metadata = {
                **asset.asset_metadata,
                "analysis": {
                    **analysis,
                    "plan": state["plan"],
                    "result": state.get("result"),
                    "narrative": state.get("narrative"),
                    "unverified_numbers": state.get("unverified_numbers", []),
                },
            }
            asset.processing_status = AssetProcessingStatus.COMPLETED
            asset.processing_error = None
            await session.commit()
        except Exception as exc:
            logger.error("analysis_failed", asset_id=str(asset_id), error_type=type(exc).__name__, exc_info=True)
            await session.rollback()
            if tracker is not None:
                await tracker.record_skipped()
            asset = await assets.get_by_id(asset_id)
            if asset is not None:
                asset.processing_status = AssetProcessingStatus.FAILED
                asset.processing_error = _analysis_error(exc)
            await session.commit()
    return {"status": "ok", "asset_id": str(asset_id)}


def _analysis_error(exc: Exception) -> str:
    """User-facing failure text. Plan and dataset errors are ours and
    safe to show (they name columns, not secrets); anything else is
    scrubbed like a report failure."""
    from app.agents.analytics.dataset import DatasetReadError
    from app.agents.analytics.nodes import AnalysisPlanError

    if isinstance(exc, (AnalysisPlanError, DatasetReadError, LookupError)):
        return str(exc)
    return scrub_report_error(exc)
```

History is stored on the analysis asset at creation by the service (Task 7), so the worker never re-queries the thread. The history list stored there already holds `{question, plan}` for the last 3 completed analyses.

- [ ] **Step 5: Run** worker, asset-service and reconciliation tests → PASS. **Step 6: Commit** `feat(analytics): analysis worker, reconciliation and dataset classification`.

---

### Task 7: Analytics API (schemas, repository, service, router)

**Files:** Create `backend/app/modules/business_analytics/{__init__,schemas,repository,service,router}.py`; Modify `backend/app/main.py`; Test `backend/tests/test_analytics_routes.py`

**Interfaces:**
- Consumes: Task 1 (`read_dataframe`, `build_profile`), Task 6 (`run_analysis`, metadata contract), `AssetRepository`, `ProjectRepository`, `get_storage_provider`.
- Produces routes (all under `/api/v1`):
  - `GET /analytics/datasets/{dataset_id}/profile` → `DatasetProfileRead`
  - `POST /analytics/datasets/{dataset_id}/analyses` body `AnalysisCreate{question: str(1..1000), sheet: str|None}` → 202 `AnalysisRead`
  - `GET /analytics/datasets/{dataset_id}/analyses` → `list[AnalysisRead]` oldest first
  - `GET /analytics/analyses/{asset_id}` → `AnalysisRead`
- `AnalysisRead`: `id, dataset_id, question, status (AssetProcessingStatus), error (str|None), plan (dict|None), result (dict|None), narrative (str|None), unverified_numbers (list[str]), created_at`.
- `DatasetProfileRead`: `row_count: int, truncated: bool, columns: list[ColumnProfileRead]` (fields as Task 1).

- [ ] **Step 1: Failing route tests** — follow `tests/test_reports_routes.py` (`_register`, `_create_project`, `httpx.ASGITransport`, cleanup in `finally`). Upload `docs/sample-data/visualization-testcases.csv` bytes via `POST /api/v1/assets/upload` (monkeypatch `app.modules.assets.service.process_uploaded_asset` to a no-op `SimpleNamespace(delay=...)`), then set its `processing_status` to COMPLETED directly in the DB. Monkeypatch `app.modules.business_analytics.service.run_analysis` to record `delay` calls. Cases:
  1. profile → 200, `row_count > 0`, cached on the dataset (`asset_metadata["profile"]`) after the call.
  2. POST analysis → 202, enqueued once, `status == "pending"`, `question` echoed.
  3. POST twice (after marking the first COMPLETED with a plan in metadata) → the second asset's stored `history` contains the first question.
  4. GET thread → both, oldest first. GET one → 200.
  5. Another user → 404 on all four routes.
  6. A DOCUMENT asset (`upload .txt`) → 409 on POST and profile.
  7. Dataset still `processing_status=QUEUED` → 409.
  8. Empty question → 422; 1001 chars → 422.

- [ ] **Step 2: Run** → fails.

- [ ] **Step 3: Implement `schemas.py`**

```python
"""Request/response models for business analytics."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.modules.assets.enums import AssetProcessingStatus
from app.modules.assets.models import Asset


class AnalysisCreate(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    sheet: str | None = Field(default=None, max_length=100)


class ColumnProfileRead(BaseModel):
    name: str
    kind: str
    dtype: str
    null_pct: float
    distinct: int
    min: Any = None
    max: Any = None
    samples: list[Any]


class DatasetProfileRead(BaseModel):
    row_count: int
    truncated: bool
    columns: list[ColumnProfileRead]


class AnalysisRead(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    question: str
    status: AssetProcessingStatus
    error: str | None
    plan: dict[str, Any] | None
    result: dict[str, Any] | None
    narrative: str | None
    unverified_numbers: list[str]
    created_at: datetime

    @classmethod
    def from_asset(cls, asset: Asset) -> "AnalysisRead":
        analysis = asset.asset_metadata.get("analysis", {})
        return cls(
            id=asset.id,
            dataset_id=analysis["dataset_id"],
            question=analysis["question"],
            status=asset.processing_status,
            error=asset.processing_error,
            plan=analysis.get("plan"),
            result=analysis.get("result"),
            narrative=analysis.get("narrative"),
            unverified_numbers=analysis.get("unverified_numbers", []),
            created_at=asset.created_at,
        )


class KaggleFileRead(BaseModel):
    name: str
    size: int


class KaggleImportRequest(BaseModel):
    owner: str = Field(pattern=r"^[A-Za-z0-9._-]+$", max_length=100)
    dataset: str = Field(pattern=r"^[A-Za-z0-9._-]+$", max_length=100)
    file_name: str = Field(min_length=1, max_length=255)


class KaggleImportAccepted(BaseModel):
    status: str = "queued"
```

- [ ] **Step 4: Implement `repository.py`**

```python
"""Persistence queries for analysis assets."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assets.enums import AssetSource, AssetType
from app.modules.assets.models import Asset

ANALYSIS_TAG = "analysis"


def dataset_tag(dataset_id: uuid.UUID) -> str:
    return f"dataset:{dataset_id}"


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_thread(self, dataset_id: uuid.UUID) -> list[Asset]:
        """Every analysis of one dataset, oldest first."""
        result = await self._session.execute(
            select(Asset)
            .where(
                Asset.source == AssetSource.GENERATED,
                Asset.asset_type == AssetType.CHART,
                Asset.tags.contains([dataset_tag(dataset_id)]),
            )
            .order_by(Asset.created_at.asc())
        )
        return list(result.scalars().all())
```

- [ ] **Step 5: Implement `service.py`**

```python
"""Business logic for business analytics. Ownership follows the reports
module: "not yours" and "doesn't exist" are the same NotFound."""

import uuid

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analytics.dataset import DatasetProfile, build_profile, read_dataframe
from app.core.config.settings import settings
from app.database.session import get_db
from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.assets.repository import AssetRepository
from app.modules.assets.storage import StorageProvider, get_storage_provider
from app.modules.business_analytics.repository import ANALYSIS_TAG, AnalysisRepository, dataset_tag
from app.workers.tasks import run_analysis

#: How many earlier completed analyses the planner sees.
HISTORY_SIZE = 3


class AnalyticsNotFoundError(Exception):
    """Asset missing or not owned by the caller."""


class DatasetNotReadyError(Exception):
    """The asset is not a dataset, or has not finished processing."""


class AnalyticsService:
    def __init__(self, session: AsyncSession, storage: StorageProvider) -> None:
        self._session = session
        self._storage = storage
        self._assets = AssetRepository(session)
        self._analyses = AnalysisRepository(session)

    async def _owned(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        asset = await self._assets.get_by_id(asset_id)
        if asset is None or asset.owner_id != owner_id:
            raise AnalyticsNotFoundError(asset_id)
        return asset

    async def get_dataset(self, owner_id: uuid.UUID, dataset_id: uuid.UUID) -> Asset:
        asset = await self._owned(owner_id, dataset_id)
        if asset.asset_type is not AssetType.DATASET or asset.processing_status is not AssetProcessingStatus.COMPLETED:
            raise DatasetNotReadyError(dataset_id)
        return asset

    async def get_profile(self, owner_id: uuid.UUID, dataset_id: uuid.UUID, sheet: str | None = None) -> DatasetProfile:
        """Cached on the dataset; computed on first request (first sheet only is cached)."""
        dataset = await self.get_dataset(owner_id, dataset_id)
        if sheet is None and "profile" in dataset.asset_metadata:
            return dataset.asset_metadata["profile"]
        content = await self._storage.read(dataset.storage_path)
        frame, truncated = read_dataframe(
            content, f".{dataset.file_extension.lstrip('.')}", sheet=sheet, max_rows=settings.analytics_max_rows
        )
        profile = build_profile(frame, truncated=truncated)
        if sheet is None:
            dataset.asset_metadata = {**dataset.asset_metadata, "profile": profile}
            await self._session.commit()
        return profile

    async def create_analysis(
        self, owner_id: uuid.UUID, dataset_id: uuid.UUID, question: str, sheet: str | None
    ) -> Asset:
        dataset = await self.get_dataset(owner_id, dataset_id)
        thread = await self._analyses.list_thread(dataset_id)
        history = [
            {"question": item.asset_metadata["analysis"]["question"], "plan": item.asset_metadata["analysis"]["plan"]}
            for item in thread
            if item.processing_status is AssetProcessingStatus.COMPLETED and item.asset_metadata["analysis"].get("plan")
        ][-HISTORY_SIZE:]
        asset = Asset(
            project_id=dataset.project_id,
            owner_id=owner_id,
            title=question[:250],
            description=None,
            asset_type=AssetType.CHART,
            status=AssetStatus.ACTIVE,
            # No file is stored: the analysis is rendered from asset_metadata.
            mime_type="application/json",
            file_name="analysis.json",
            file_extension="json",
            file_size=0,
            storage_path="",
            checksum="",
            source=AssetSource.GENERATED,
            version=1,
            tags=[ANALYSIS_TAG, dataset_tag(dataset_id)],
            asset_metadata={
                "analysis": {
                    "dataset_id": str(dataset_id), "question": question, "sheet": sheet,
                    "history": history, "plan": None, "result": None,
                    "narrative": None, "unverified_numbers": [],
                }
            },
            ai_profile=AIProfile().model_dump(mode="json"),
            created_by=owner_id,
            processing_status=AssetProcessingStatus.PENDING,
        )
        created = await self._assets.create(asset)
        await self._session.commit()
        run_analysis.delay(str(created.id))
        return created

    async def list_analyses(self, owner_id: uuid.UUID, dataset_id: uuid.UUID) -> list[Asset]:
        await self._owned(owner_id, dataset_id)
        return await self._analyses.list_thread(dataset_id)

    async def get_analysis(self, owner_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
        asset = await self._owned(owner_id, asset_id)
        if ANALYSIS_TAG not in asset.tags:
            raise AnalyticsNotFoundError(asset_id)
        return asset


async def get_analytics_service(session: AsyncSession = Depends(get_db)) -> AnalyticsService:
    return AnalyticsService(session, get_storage_provider())
```

- [ ] **Step 6: Implement `router.py`**

```python
"""HTTP routes for business analytics (Milestone 6).

Step traces and live streams reuse `/reports/{asset_id}/steps*`, which
already serve any owned GENERATED asset."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.analytics.dataset import DatasetReadError
from app.modules.auth.models import User
from app.modules.auth.security import get_current_user
from app.modules.business_analytics.schemas import AnalysisCreate, AnalysisRead, DatasetProfileRead
from app.modules.business_analytics.service import (
    AnalyticsNotFoundError,
    AnalyticsService,
    DatasetNotReadyError,
    get_analytics_service,
)

router = APIRouter(prefix="/analytics", tags=["Business Analytics"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")
_NOT_READY = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="This asset is not a processed CSV/XLSX dataset yet.",
)


@router.get("/datasets/{dataset_id}/profile", response_model=DatasetProfileRead)
async def get_profile(
    dataset_id: uuid.UUID,
    sheet: str | None = None,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> DatasetProfileRead:
    try:
        return DatasetProfileRead.model_validate(await service.get_profile(current_user.id, dataset_id, sheet))
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc
    except DatasetNotReadyError as exc:
        raise _NOT_READY from exc
    except DatasetReadError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/datasets/{dataset_id}/analyses", response_model=AnalysisRead, status_code=status.HTTP_202_ACCEPTED)
async def create_analysis(
    dataset_id: uuid.UUID,
    data: AnalysisCreate,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalysisRead:
    try:
        asset = await service.create_analysis(current_user.id, dataset_id, data.question.strip(), data.sheet)
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc
    except DatasetNotReadyError as exc:
        raise _NOT_READY from exc
    return AnalysisRead.from_asset(asset)


@router.get("/datasets/{dataset_id}/analyses", response_model=list[AnalysisRead])
async def list_analyses(
    dataset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> list[AnalysisRead]:
    try:
        return [AnalysisRead.from_asset(asset) for asset in await service.list_analyses(current_user.id, dataset_id)]
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc


@router.get("/analyses/{asset_id}", response_model=AnalysisRead)
async def get_analysis(
    asset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnalysisRead:
    try:
        return AnalysisRead.from_asset(await service.get_analysis(current_user.id, asset_id))
    except AnalyticsNotFoundError as exc:
        raise _NOT_FOUND from exc
```

`main.py`: `from app.modules.business_analytics.router import router as analytics_router` and `app.include_router(analytics_router, prefix=settings.api_v1_prefix)` after `reports_router`.

- [ ] **Step 7: Run** route tests → PASS. **Step 8: Commit** `feat(analytics): business analytics API`.

---

### Task 8: Kaggle import

**Files:** Create `backend/app/integrations/kaggle/{__init__,client}.py`; Modify `business_analytics/{service,router}.py`, `workers/tasks.py`; Test `backend/tests/test_kaggle_client.py` (+ 2 route cases in `test_analytics_routes.py`)

**Interfaces:**
- Produces: `KaggleClient(username: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float)`, `list_files(owner, dataset) -> list[KaggleFile]`, `download_file(owner, dataset, file_name, *, max_bytes) -> bytes`, `get_kaggle_client() -> KaggleClient` (raises `KaggleNotConfiguredError`), `KaggleError`, `KaggleFileTooLargeError(KaggleError)`, `KaggleFile(name: str, size: int)` dataclass.
- Routes: `GET /analytics/kaggle/{owner}/{dataset}/files` → `list[KaggleFileRead]`; `POST /projects/{project_id}/analytics/kaggle/import` `KaggleImportRequest` → 202 `KaggleImportAccepted`. 503 when not configured; 502 on Kaggle errors; 404 on project not owned.
- Celery: `workers.import_kaggle_dataset(project_id, owner_id, owner, dataset, file_name)`.

- [ ] **Step 1: Verify the Kaggle REST endpoints** against the current public API (the `kaggle` CLI's swagger, `kaggle/api/kaggle_api.py` on GitHub). Expected: `GET https://www.kaggle.com/api/v1/datasets/list/{owner}/{dataset}` → `{"datasetFiles": [{"name", "totalBytes"}]}` and `GET https://www.kaggle.com/api/v1/datasets/download/{owner}/{dataset}/{fileName}` → file bytes (sometimes a zip). Basic auth with username/key. If they differ, use the verified paths/fields in the code and tests below.

- [ ] **Step 2: Failing client tests** using `httpx.MockTransport`:

```python
import io
import zipfile

import httpx
import pytest

from app.integrations.kaggle.client import KaggleClient, KaggleError, KaggleFileTooLargeError


def client(handler) -> KaggleClient:
    return KaggleClient("user", "key", transport=httpx.MockTransport(handler), timeout=5)


async def test_lists_only_tabular_files():
    def handler(request):
        assert request.headers["authorization"].startswith("Basic ")
        assert request.url.path == "/api/v1/datasets/list/acme/sales"
        return httpx.Response(200, json={"datasetFiles": [
            {"name": "sales.csv", "totalBytes": 10}, {"name": "readme.md", "totalBytes": 3}, {"name": "q.xlsx", "totalBytes": 7}]})
    files = await client(handler).list_files("acme", "sales")
    assert [(f.name, f.size) for f in files] == [("sales.csv", 10), ("q.xlsx", 7)]


async def test_download_plain_file():
    content = b"a,b\n1,2\n"
    files = await client(lambda r: httpx.Response(200, content=content)).download_file("acme", "sales", "sales.csv", max_bytes=100)
    assert files == content


async def test_download_unzips():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sales.csv", "a\n1\n")
    data = await client(lambda r: httpx.Response(200, content=buffer.getvalue())).download_file("acme", "sales", "sales.csv", max_bytes=100)
    assert data == b"a\n1\n"


async def test_download_over_limit_aborts():
    with pytest.raises(KaggleFileTooLargeError):
        await client(lambda r: httpx.Response(200, content=b"x" * 101)).download_file("acme", "sales", "s.csv", max_bytes=100)


async def test_http_error_raises_kaggle_error():
    with pytest.raises(KaggleError):
        await client(lambda r: httpx.Response(404)).list_files("acme", "missing")


async def test_rejects_bad_slug():
    with pytest.raises(KaggleError):
        await client(lambda r: httpx.Response(200)).list_files("../etc", "x")
```

- [ ] **Step 3: Implement `client.py`**

```python
"""Minimal Kaggle REST client for importing one dataset file.

Talks to the public API directly with httpx (basic auth) instead of the
`kaggle` package, which would add a dependency for two calls."""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from app.core.config.settings import settings

KAGGLE_API = "https://www.kaggle.com/api/v1"
_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")
TABULAR_SUFFIXES = (".csv", ".xlsx", ".xls")


class KaggleError(Exception):
    """Kaggle refused the request or returned something unusable."""


class KaggleNotConfiguredError(KaggleError):
    """No Kaggle credentials in the environment."""


class KaggleFileTooLargeError(KaggleError):
    """The file exceeds the import size limit."""


@dataclass(frozen=True)
class KaggleFile:
    name: str
    size: int


def _check_slug(*values: str) -> None:
    for value in values:
        if not _SLUG.fullmatch(value):
            raise KaggleError(f"Invalid Kaggle identifier '{value}'.")


class KaggleClient:
    def __init__(self, username: str, key: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float) -> None:
        self._auth = httpx.BasicAuth(username, key)
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=KAGGLE_API, auth=self._auth, transport=self._transport, timeout=self._timeout, follow_redirects=True)

    async def list_files(self, owner: str, dataset: str) -> list[KaggleFile]:
        _check_slug(owner, dataset)
        async with self._client() as client:
            response = await client.get(f"/datasets/list/{owner}/{dataset}")
        if response.status_code != 200:
            raise KaggleError(f"Kaggle returned {response.status_code} listing {owner}/{dataset}.")
        return [
            KaggleFile(name=item["name"], size=int(item.get("totalBytes") or 0))
            for item in response.json().get("datasetFiles", [])
            if item["name"].lower().endswith(TABULAR_SUFFIXES)
        ]

    async def download_file(self, owner: str, dataset: str, file_name: str, *, max_bytes: int) -> bytes:
        _check_slug(owner, dataset)
        if not file_name.lower().endswith(TABULAR_SUFFIXES):
            raise KaggleError("Only CSV and XLSX files can be imported.")
        buffer = bytearray()
        async with self._client() as client:
            async with client.stream("GET", f"/datasets/download/{owner}/{dataset}/{quote(file_name, safe='')}") as response:
                if response.status_code != 200:
                    raise KaggleError(f"Kaggle returned {response.status_code} downloading {file_name}.")
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        raise KaggleFileTooLargeError(f"{file_name} is larger than {max_bytes // (1024 * 1024)} MB.")
        return _unzip(bytes(buffer), file_name, max_bytes)


def _unzip(content: bytes, file_name: str, max_bytes: int) -> bytes:
    """Kaggle serves some single files zipped; return the member itself."""
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        wanted = PurePosixPath(file_name).name
        member = next((info for info in archive.infolist() if PurePosixPath(info.filename).name == wanted), None)
        if member is None:
            raise KaggleError(f"{file_name} was not found in Kaggle's archive.")
        if member.file_size > max_bytes:
            raise KaggleFileTooLargeError(f"{file_name} is larger than {max_bytes // (1024 * 1024)} MB.")
        return archive.read(member)


def get_kaggle_client() -> KaggleClient:
    if not settings.kaggle_username or settings.kaggle_key is None:
        raise KaggleNotConfiguredError("Kaggle is not configured.")
    return KaggleClient(settings.kaggle_username, settings.kaggle_key.get_secret_value(), timeout=settings.kaggle_timeout)
```

`__init__.py`: one-line docstring `"""Kaggle dataset import integration (Milestone 6)."""`.

- [ ] **Step 4: Worker task** in `workers/tasks.py`:

```python
_MIME_BY_SUFFIX = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
}


@celery_app.task(name="workers.import_kaggle_dataset", bind=True, max_retries=0)
@log_task_execution
def import_kaggle_dataset(self, project_id: str, owner_id: str, owner: str, dataset: str, file_name: str) -> dict[str, Any]:
    """Download one Kaggle file and process it as a DATASET asset, the
    same path an OpenAlex paper import takes (`add_open_access_paper`)."""
    return _run_task_loop(_import_kaggle_dataset(uuid.UUID(project_id), uuid.UUID(owner_id), owner, dataset, file_name))


async def _import_kaggle_dataset(project_id: uuid.UUID, owner_id: uuid.UUID, owner: str, dataset: str, file_name: str) -> dict[str, Any]:
    from pathlib import PurePosixPath

    from app.integrations.kaggle.client import KaggleError, get_kaggle_client
    from app.modules.assets.enums import AssetType
    from app.modules.assets.service import AssetService, DuplicateAssetError

    max_bytes = min(settings.analytics_max_file_mb, settings.max_upload_size_mb) * 1024 * 1024
    base_name = PurePosixPath(file_name).name
    try:
        content = await get_kaggle_client().download_file(owner, dataset, file_name, max_bytes=max_bytes)
    except KaggleError as exc:
        logger.warning("kaggle_import_failed", owner=owner, dataset=dataset, reason=str(exc))
        return {"status": "failed", "reason": str(exc)}

    async with async_session_factory() as session:
        storage = get_storage_provider()
        try:
            asset = await AssetService(session, storage).create_imported_asset(
                owner_id=owner_id, project_id=project_id, content=content, file_name=base_name,
                mime_type=_MIME_BY_SUFFIX[PurePosixPath(base_name).suffix.lower()],
                title=f"{owner}/{dataset}: {base_name}", asset_type=AssetType.DATASET,
            )
        except DuplicateAssetError as exc:
            return {"status": "added", "asset_id": str(exc.existing_asset.id)}
        except AssetValidationError as exc:
            return {"status": "failed", "reason": str(exc)}
        await get_asset_processing_service(session, storage).process_asset(asset.id)
    logger.info("kaggle_import_done", asset_id=str(asset.id), owner=owner, dataset=dataset)
    return {"status": "added", "asset_id": str(asset.id)}
```

- [ ] **Step 5: Service + routes.** In `service.py` add:

```python
class ProjectAccessDeniedError(Exception):
    """Project missing or not owned by the caller."""

    # in AnalyticsService:
    async def list_kaggle_files(self, owner: str, dataset: str) -> list[KaggleFile]:
        return await get_kaggle_client().list_files(owner, dataset)

    async def import_from_kaggle(self, owner_id: uuid.UUID, project_id: uuid.UUID, request: KaggleImportRequest) -> None:
        project = await ProjectRepository(self._session).get_by_id(project_id)
        if project is None or project.owner_id != owner_id:
            raise ProjectAccessDeniedError(project_id)
        get_kaggle_client()  # fail fast with 503 before queueing
        import_kaggle_dataset.delay(str(project_id), str(owner_id), request.owner, request.dataset, request.file_name)
```

(Imports: `ProjectRepository` from `app.modules.projects.repository`; `KaggleFile`, `get_kaggle_client` from the client; `import_kaggle_dataset` from `app.workers.tasks`; `KaggleImportRequest` from schemas.)

Router — the import route is project-scoped, so add a second router without the `/analytics` prefix, following the reports router's "full paths" style; include both in `main.py`:

```python
project_router = APIRouter(tags=["Business Analytics"])


def _kaggle_http_error(exc: KaggleError) -> HTTPException:
    if isinstance(exc, KaggleNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Kaggle is not configured.")
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/kaggle/{owner}/{dataset}/files", response_model=list[KaggleFileRead])
async def list_kaggle_files(
    owner: str, dataset: str,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> list[KaggleFileRead]:
    try:
        return [KaggleFileRead(name=item.name, size=item.size) for item in await service.list_kaggle_files(owner, dataset)]
    except KaggleError as exc:
        raise _kaggle_http_error(exc) from exc


@project_router.post("/projects/{project_id}/analytics/kaggle/import", response_model=KaggleImportAccepted, status_code=status.HTTP_202_ACCEPTED)
async def import_kaggle(
    project_id: uuid.UUID, data: KaggleImportRequest,
    current_user: User = Depends(get_current_user),
    service: AnalyticsService = Depends(get_analytics_service),
) -> KaggleImportAccepted:
    try:
        await service.import_from_kaggle(current_user.id, project_id, data)
    except ProjectAccessDeniedError as exc:
        raise _NOT_FOUND from exc
    except KaggleError as exc:
        raise _kaggle_http_error(exc) from exc
    return KaggleImportAccepted()
```

Route tests: unconfigured (monkeypatch `settings.kaggle_username=None`) → 503 on both; configured + enqueue monkeypatched → 202 and one `delay` call; other user's project → 404.

- [ ] **Step 6: Run** Kaggle + route tests → PASS. **Step 7: Commit** `feat(analytics): Kaggle dataset import`.

---

### Task 9: Frontend — service, chart mapping, workspace, Kaggle dialog, tab

**Files:**
- Regenerate: `frontend/src/types/api.d.ts` (`npm run generate:api` with the backend running on 8001)
- Create: `frontend/src/services/analytics.ts`, `frontend/src/features/business-analytics/{analysis-chart.ts,analysis-chart.test.ts,AnalyticsWorkspace.tsx,DatasetProfile.tsx,AnalysisCard.tsx,AnalysisCard.test.tsx,KaggleImportDialog.tsx,KaggleImportDialog.test.tsx}`
- Modify: `frontend/src/pages/ProjectDetail.tsx` (TABS + TabsContent)

**Interfaces:**
- Consumes: routes from Tasks 7–8; `listAssets(projectId)`; `WorkflowTimeline({owner:{kind:"report", id}, active})`; default export `PlotlyVisualization({data, layout})` (lazy); `request` from `@/services/client`.
- Produces: `chartFigure(result, chart) -> {data, layout} | null` (pure), components `AnalyticsWorkspace({projectId})`, `AnalysisCard({analysis})`, `KaggleImportDialog({projectId, onImported})`.

- [ ] **Step 1: Service** `services/analytics.ts`

```ts
import { request } from "@/services/client";
import type { components } from "@/types/api";

export type AnalysisRead = components["schemas"]["AnalysisRead"];
export type DatasetProfileRead = components["schemas"]["DatasetProfileRead"];
type KaggleFileRead = components["schemas"]["KaggleFileRead"];

export const getProfile = (datasetId: string) =>
  request<DatasetProfileRead>(`/api/v1/analytics/datasets/${datasetId}/profile`);

export const listAnalyses = (datasetId: string) =>
  request<AnalysisRead[]>(`/api/v1/analytics/datasets/${datasetId}/analyses`);

export const createAnalysis = (datasetId: string, question: string) =>
  request<AnalysisRead>(`/api/v1/analytics/datasets/${datasetId}/analyses`, {
    method: "POST",
    body: { question },
  });

export const listKaggleFiles = (owner: string, dataset: string) =>
  request<KaggleFileRead[]>(`/api/v1/analytics/kaggle/${encodeURIComponent(owner)}/${encodeURIComponent(dataset)}/files`);

export const importKaggle = (projectId: string, owner: string, dataset: string, fileName: string) =>
  request<{ status: string }>(`/api/v1/projects/${projectId}/analytics/kaggle/import`, {
    method: "POST",
    body: { owner, dataset, file_name: fileName },
  });
```

- [ ] **Step 2: Failing chart-mapping tests** `analysis-chart.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { chartFigure } from "./analysis-chart";

const result = { columns: ["region", "rev"], rows: [{ region: "West", rev: 150 }, { region: "East", rev: 300 }], total_rows: 2 };

describe("chartFigure", () => {
  it("maps bar charts to one bar trace per y", () => {
    const figure = chartFigure(result, { type: "bar", x: "region", y: ["rev"], series: null });
    expect(figure?.data).toEqual([{ type: "bar", name: "rev", x: ["West", "East"], y: [150, 300] }]);
  });
  it("maps pie charts to labels/values", () => {
    const figure = chartFigure(result, { type: "pie", x: "region", y: ["rev"], series: null });
    expect(figure?.data[0]).toMatchObject({ type: "pie", labels: ["West", "East"], values: [150, 300] });
  });
  it("splits a series column into one trace per value", () => {
    const rows = [{ m: "Jan", r: "W", v: 1 }, { m: "Jan", r: "E", v: 2 }, { m: "Feb", r: "W", v: 3 }];
    const figure = chartFigure({ columns: ["m", "r", "v"], rows, total_rows: 3 }, { type: "line", x: "m", y: ["v"], series: "r" });
    expect(figure?.data.map((trace) => trace.name)).toEqual(["W", "E"]);
    expect(figure?.data[0]).toMatchObject({ type: "scatter", mode: "lines+markers", x: ["Jan", "Feb"], y: [1, 3] });
  });
  it("returns null for table and kpi", () => {
    expect(chartFigure(result, { type: "table", x: null, y: [], series: null })).toBeNull();
    expect(chartFigure(result, { type: "kpi", x: null, y: ["rev"], series: null })).toBeNull();
  });
});
```

- [ ] **Step 3: Implement** `analysis-chart.ts`

```ts
type Row = Record<string, unknown>;
export type AnalysisResultData = { columns: string[]; rows: Row[]; total_rows: number };
export type ChartSpecData = { type: "bar" | "line" | "pie" | "table" | "kpi"; x: string | null; y: string[]; series: string | null };
type Trace = Record<string, unknown> & { name?: string };

/** Plotly traces for an analysis result; `null` when the chart type is
 * rendered as HTML instead (table, kpi). Pure, so it is unit-tested. */
export function chartFigure(result: AnalysisResultData, chart: ChartSpecData): { data: Trace[]; layout: Record<string, unknown> } | null {
  if (chart.type === "table" || chart.type === "kpi" || !chart.x) return null;
  const x = chart.x;
  if (chart.type === "pie") {
    const y = chart.y[0];
    return { data: [{ type: "pie", labels: result.rows.map((row) => row[x]), values: result.rows.map((row) => row[y]) }], layout: {} };
  }
  const base = chart.type === "line" ? { type: "scatter", mode: "lines+markers" } : { type: "bar" };
  const groups = chart.series
    ? [...new Set(result.rows.map((row) => String(row[chart.series!])))].map((value) => ({
        name: value,
        rows: result.rows.filter((row) => String(row[chart.series!]) === value),
      }))
    : null;
  const data: Trace[] = groups
    ? groups.map(({ name, rows }) => ({ ...base, name, x: rows.map((row) => row[x]), y: rows.map((row) => row[chart.y[0]]) }))
    : chart.y.map((y) => ({ ...base, name: y, x: result.rows.map((row) => row[x]), y: result.rows.map((row) => row[y]) }));
  return { data, layout: { xaxis: { title: { text: x } }, barmode: "group", showlegend: data.length > 1 } };
}
```

- [ ] **Step 4: `AnalysisCard.tsx`** — renders one analysis:
  - header: question + `StatusBadge` (from `@/components/common/StatusBadge`, as `ReportRunPanel` uses);
  - while not `completed`/`failed`: `<WorkflowTimeline owner={{ kind: "report", id: analysis.id }} active />`;
  - failed: `analysis.error` in a destructive-tone paragraph;
  - completed: `kpi` → big number(s) from `result.rows[0][y]`; otherwise `chartFigure` → lazy `PlotlyVisualization` (`const PlotlyVisualization = lazy(() => import("@/features/research/PlotlyVisualization"))` inside `Suspense`); result table in `<details>` with "Showing {rows.length} of {total_rows}" when truncated;
  - narrative: split on unverified tokens and wrap each in `<mark title="Not found in the result" className="underline decoration-dotted bg-transparent">`; when completed with no narrative and a result, show "Explanation unavailable.";
  - `<details><summary>How this was computed</summary>` listing filters (`column op value`), time bucket, group by, metrics (`agg(column) as alias`, "% of total" when `as_share`), sort, limit.
  Keep styling consistent with `ReportRunPanel` (Tailwind tokens already used there).

  Tests (`AnalysisCard.test.tsx`, following existing component tests with `@testing-library/react`; mock `@/features/research/PlotlyVisualization` with a stub `div data-testid="plot"` and `@/features/workflow-timeline/WorkflowTimeline` with a stub): completed bar → plot stub + narrative; unverified number wrapped in `mark`; failed → error text; unanswerable (`result: null`, narrative present) → narrative, no plot; kpi → big number text; "How this was computed" lists `sum(revenue) as total`.

- [ ] **Step 5: `DatasetProfile.tsx`** — `useQuery(["analytics-profile", datasetId], () => getProfile(datasetId))`; `<details open>` with "{row_count} rows · {n} columns" (+ "truncated" note) and a table: name, kind, null %, samples joined by ", ".

- [ ] **Step 6: `KaggleImportDialog.tsx`** — Radix dialog (same `Dialog` UI components `BuildPlanDialog` uses). Input "owner/dataset" → "Find files" calls `listKaggleFiles`; error 503 → text "Kaggle import isn't configured on this server."; radio list of files with sizes (`formatBytes` from `@/lib/format` if present, else KB/MB inline); "Import" calls `importKaggle` then `onImported()` and closes with a toast-free inline "Import started — the dataset appears when processing finishes." Tests: lists files after search; import calls service with the chosen file; 503 shows the not-configured message (mock `@/services/analytics`).

- [ ] **Step 7: `AnalyticsWorkspace.tsx`**
  - `useQuery(["assets", projectId], () => listAssets(projectId))` filtered to `asset_type === "dataset"`; poll every 3 s while any dataset is not `completed`/`failed`/`unsupported` (use `refetchInterval` function), so Kaggle imports appear;
  - empty state: "Upload a CSV or Excel file in Documents, or import one from Kaggle." with the Kaggle button;
  - dataset `<select>` (native, per ladder) of completed datasets; selected id in state;
  - `DatasetProfile`, then thread: `useQuery(["analyses", datasetId], () => listAnalyses(datasetId), { refetchInterval: (q) => q.state.data?.some(a => a.status === "pending" || a.status === "running") ? 2000 : false })` → `AnalysisCard` per item;
  - question `<form>`: textarea (maxLength 1000) + "Ask" button → `useMutation(createAnalysis)` → `invalidateQueries(["analyses", datasetId])`; show `messageFor(error)` from `@/lib/api-error` on failure.

- [ ] **Step 8: Tab** in `ProjectDetail.tsx`: add `{ value: "analytics", label: "Analytics" }` to `TABS` and

```tsx
        <TabsContent value="analytics">
          {tab === "analytics" && <AnalyticsWorkspace projectId={project.id} />}
        </TabsContent>
```

(use whatever variable holds the project id in that file.)

- [ ] **Step 9: Run** `npx vitest run src/features/business-analytics` and `npx tsc -b` → PASS/clean. **Step 10: Commit** `feat(frontend): business analytics workspace`.

---

### Task 10: End-to-end verification

- [ ] Full backend suite: `cd backend && .venv/Scripts/python -m pytest -q` — record pass/fail counts; failures unrelated to analytics that also fail on `main` are reported, not fixed.
- [ ] Full frontend suite: `cd frontend && npx vitest run` and `npm run build`.
- [ ] Start the app (existing dev start script / `.claude/launch.json`), open the project's Analytics tab in the browser preview, upload `docs/sample-data/visualization-testcases.csv`, ask a question, then a follow-up refining it; confirm live steps, chart, table, narrative, and "How this was computed". Screenshot as proof.
- [ ] `graphify update .` (if installed) and commit any graph changes separately: `chore: update knowledge graph`.

---

## Self-Review

- Spec coverage: scope/engine/persistence/follow-ups (Tasks 5–7), plan contract + validation + difflib (2), executor order + no eval (3), limits + profile (1, 6), explain non-critical + validate (4, 5), API (7), step endpoints reused (7 router doc, 9 `kind:"report"`), Kaggle (8), errors 404/409/422/503 + reconciliation (6–8), frontend (9), testing (every task), out-of-scope untouched.
- Types consistent: `AnalysisResult{columns, rows, total_rows}`, `asset_metadata["analysis"]` keys, `AnalyticsGraphDependencies(storage, llm_gateway)`, `ReportStepTracker(..., registry=ANALYTICS_AGENT_REGISTRY)` used identically across Tasks 5–7.
