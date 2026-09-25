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
    # R1: profile min/max use exactly what `to_json(date_format="iso")` emits.
    assert columns["date"]["min"] == "2024-01-05T00:00:00.000"
    assert columns["region"]["kind"] == "text"
    assert columns["region"]["distinct"] == 2
    assert len(columns["region"]["samples"]) <= 5
