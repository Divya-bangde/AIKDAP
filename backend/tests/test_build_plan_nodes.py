"""Unit tests for the build-plan graph's nodes (Milestone 10 step 5).

Every node runs against fakes -- a fake LLM gateway (the `FakeGateway`
shape `tests/test_paper_suggestion.py` established), fake providers, and
a fake section searcher. No live model call, no network, no database.
"""

import json

import pytest

from app.agents.reports.build_plan_graph import get_build_plan_graph
from app.agents.reports.build_plan_registry import BUILD_PLAN_AGENT_REGISTRY
from app.agents.reports.build_plan_state import BUILD_PLAN_SECTION_TITLES, BuildPlanNode


def test_registry_declares_the_four_nodes_with_research_non_critical():
    assert list(BUILD_PLAN_AGENT_REGISTRY) == [
        BuildPlanNode.EXTRACT.value,
        BuildPlanNode.RESEARCH_FURTHER.value,
        BuildPlanNode.RECOMMEND_TOOLS.value,
        BuildPlanNode.RECOMMEND_PROCESS.value,
    ]
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.EXTRACT.value].critical is True
    # Spec section 6/7: a missing or failing provider must not fail the report.
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RESEARCH_FURTHER.value].critical is False
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RECOMMEND_TOOLS.value].critical is True
    assert BUILD_PLAN_AGENT_REGISTRY[BuildPlanNode.RECOMMEND_PROCESS.value].critical is True


def test_graph_compiles_with_every_registered_node():
    graph = get_build_plan_graph()
    assert set(BUILD_PLAN_AGENT_REGISTRY) <= set(graph.get_graph().nodes)


def test_section_titles_are_the_five_document_sections():
    assert BUILD_PLAN_SECTION_TITLES == [
        "What the Paper Builds",
        "Follow-Up Research",
        "Implementations and Resources",
        "Recommended Tools",
        "Build Process",
    ]
