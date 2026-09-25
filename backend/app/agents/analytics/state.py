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
