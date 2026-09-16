"""Graph nodes for build-plan generation, and the strategies they
execute against (Milestone 10 step 5 -- spec section 6).

Mirrors `agents.reports.nodes`'s shape: abstractions and the dependency
bundle first, then the nodes, injected through
`config["configurable"]["dependencies"]`.
"""

from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm.gateway import LLMGateway, get_llm_gateway


@dataclass(frozen=True)
class BuildPlanDependencies:
    """The strategies one build-plan run executes against."""

    llm_gateway: LLMGateway


def build_build_plan_dependencies(session: AsyncSession) -> BuildPlanDependencies:
    """Assemble the default dependency set for a database-backed run."""
    return BuildPlanDependencies(llm_gateway=get_llm_gateway())


def _dependencies(config: RunnableConfig) -> BuildPlanDependencies:
    dependencies = (config.get("configurable") or {}).get("dependencies")
    if not isinstance(dependencies, BuildPlanDependencies):
        raise RuntimeError(
            "Build-plan graph invoked without a BuildPlanDependencies instance in "
            "config['configurable']['dependencies']."
        )
    return dependencies


async def extract_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def research_further_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def recommend_tools_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError


async def recommend_process_node(state, config: RunnableConfig) -> dict[str, Any]:
    raise NotImplementedError
