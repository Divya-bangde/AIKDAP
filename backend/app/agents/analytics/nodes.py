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
