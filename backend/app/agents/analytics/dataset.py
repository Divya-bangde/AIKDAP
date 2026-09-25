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
