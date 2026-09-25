"""Prompts for the analytics graph."""

import json
from typing import Any

from app.agents.analytics.dataset import DatasetProfile
from app.agents.analytics.plan import AnalysisPlan
from app.agents.analytics.state import HistoryItem

PLAN_SYSTEM_PROMPT = f"""You translate a business question about ONE tabular dataset into a JSON analysis plan.
Rules:
- Use only column names from the dataset profile, spelled exactly.
- sum/mean/median only on numeric columns. count may omit "column" to count rows.
- time_bucket only on datetime columns; the bucketed column keeps its name.
- Output columns are: the time_bucket column, then group_by columns, then metric aliases.
  sort.by and every chart field must be output columns.
- Chart: "line" for trends over time, "bar" for comparing categories, "pie" for shares
  (use as_share), "kpi" for a single number, "table" otherwise.
- If the dataset cannot answer the question, return only {{"unanswerable_reason": "<why, one sentence>"}}.
The plan must match this JSON schema, using exactly these field names:
{json.dumps(AnalysisPlan.model_json_schema())}
Return JSON only."""

EXPLAIN_SYSTEM_PROMPT = """You explain the result of a data analysis to a business user in at most 120 words.
Use ONLY numbers that appear in the result table; round sensibly. Lead with the direct answer.
If the prompt says Truncated: yes, say so briefly; otherwise never mention truncation. Plain text, no markdown headings."""


def render_plan_prompt(*, question: str, profile: DatasetProfile, history: list[HistoryItem], errors: list[str] | None = None) -> str:
    parts = [f"Dataset profile:\n{json.dumps(profile, default=str)}"]
    if history:
        earlier = "\n".join(f"- Q: {item['question']}\n  plan: {json.dumps(item['plan'])}" for item in history)
        parts.append(f"Earlier questions on this dataset (the new question may refine them):\n{earlier}")
    parts.append(f"Question: {question}")
    if errors:
        parts.append("Your previous plan was invalid. Fix these problems:\n" + "\n".join(f"- {error}" for error in errors))
    return "\n\n".join(parts)


def render_explain_prompt(*, question: str, result: dict[str, Any], profile_truncated: bool) -> str:
    shown = result["rows"][:50]
    rows_hidden = result["total_rows"] > len(shown)
    note = f" (showing {len(shown)} of {result['total_rows']} rows)" if rows_hidden else ""
    truncated = profile_truncated or rows_hidden
    reason = "dataset row cap at load" if profile_truncated else "result rows beyond those shown" if rows_hidden else None
    truncation_line = f"Truncated: yes ({reason})" if truncated else "Truncated: no"
    return f"Question: {question}\n\nResult table{note}:\n{json.dumps(shown)}\n\n{truncation_line}"
