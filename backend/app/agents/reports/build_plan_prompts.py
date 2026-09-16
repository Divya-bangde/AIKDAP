"""Prompt templates for the build-plan graph's four LLM calls
(Milestone 10 step 5 -- spec section 6).

Every prompt carries the same two hard rules the spec sets: ground
everything in the provided material, and never write a URL. Links are
appended deterministically from provider results by the nodes
themselves (design ruling R5), so a model that invents one produces
nothing a reader can click.
"""

from app.agents.reports.build_plan_state import (
    BuildPhase,
    PaperFindings,
    ResearchLink,
    TOOL_STAGES,
    ToolRecommendation,
)
from app.agents.reports.state import ProcessedDocument, SectionEvidence

#: Repeated in every system prompt below. Stated once so the four
#: prompts cannot drift on the rule that matters most.
_NO_LINKS_RULE = (
    "Never write a URL, link, DOI, or web address of any kind -- not in "
    "prose, not in parentheses, not as a citation. Links are added "
    "separately from verified search results. Any URL you write will be "
    "deleted before the reader sees it."
)

EXTRACT_SYSTEM_PROMPT = (
    "You read a research paper the user wants to turn into a real "
    "project, and you report only what the paper itself says. Base every "
    "field strictly on the provided excerpts. If the excerpts do not "
    "state something, return an empty list for that field rather than "
    "guessing a plausible value. " + _NO_LINKS_RULE
)

TOOLS_SYSTEM_PROMPT = (
    "You recommend concrete, well-known tools and libraries for building "
    "the described system. Every recommendation must name a real tool and "
    "give a reason that refers to something specific in the paper's "
    "findings -- a named model, dataset, metric, or compute need. Offer "
    "alternatives so the builder is not locked in. " + _NO_LINKS_RULE
)

PROCESS_SYSTEM_PROMPT = (
    "You lay out the phases of building a working product from a research "
    "paper, starting from reproducing the paper's own baseline. Each phase "
    "gets concrete steps, one checkable definition of done, and the risks "
    "that phase actually carries -- for example a dataset that needs "
    "licence approval before it can be used. " + _NO_LINKS_RULE
)


def render_extract_prompt(
    *, documents: list[ProcessedDocument], evidence: dict[str, list[SectionEvidence]]
) -> str:
    """One prompt for one structured call that fills every field.

    Field-scoped excerpts are labelled with the field they were
    retrieved for, so the model sees which evidence answers which
    question instead of one undifferentiated pile.
    """
    document_lines = "\n".join(
        f"- [{document['asset_id']}] {document['title']}: "
        f"{document['summary'] or 'No summary available.'}"
        for document in documents
    )
    evidence_blocks = []
    for field, items in evidence.items():
        if not items:
            continue
        excerpts = "\n".join(f"  - [{item['asset_id']}] {item['snippet']}" for item in items)
        evidence_blocks.append(f"Excerpts retrieved for '{field}':\n{excerpts}")
    evidence_block = "\n\n".join(evidence_blocks) or "No excerpts available."

    return (
        f"Selected papers:\n{document_lines}\n\n"
        f"{evidence_block}\n\n"
        "From these papers only, report what would have to be built. "
        "Return JSON with these keys, each a list of short statements: "
        "'method' (the method or algorithm proposed), 'models' (the models "
        "or architectures used), 'datasets' (the datasets used), 'metrics' "
        "(the evaluation metrics), 'compute' (hardware, GPU, or training-time "
        "needs), and 'limitations' (the limitations the authors themselves "
        "state). Use an empty list for anything the excerpts do not state."
    )


def render_tools_prompt(*, findings: PaperFindings, links: list[ResearchLink]) -> str:
    """Prompt for the per-stage tool recommendations.

    `links` are passed as titles and notes only -- never URLs -- so the
    model can take a real implementation or library into account without
    ever being handed a URL it might echo back.
    """
    findings_block = _findings_block(findings)
    resources = (
        "\n".join(f"- {link['title']}: {link['note']}" for link in links)
        or "No external search results were available."
    )
    stages = ", ".join(TOOL_STAGES)
    return (
        f"Findings from the paper:\n{findings_block}\n\n"
        f"Resources found by search (titles only):\n{resources}\n\n"
        f"Recommend tools for building this system, grouped by stage. "
        f"Use exactly these stage values: {stages}. Return JSON with a "
        "'tools' list; each item has 'stage' (one of the stage values), "
        "'name' (the tool), 'reason' (why, referring to a specific "
        "finding above), and 'alternatives' (a list of other tools that "
        "would also work). Cover every stage that the findings support."
    )


def render_process_prompt(*, findings: PaperFindings, tools: list[ToolRecommendation]) -> str:
    """Prompt for the phased build process."""
    findings_block = _findings_block(findings)
    tool_lines = (
        "\n".join(f"- {tool['stage']}: {tool['name']} ({tool['reason']})" for tool in tools)
        or "No tools were recommended."
    )
    return (
        f"Findings from the paper:\n{findings_block}\n\n"
        f"Recommended tools:\n{tool_lines}\n\n"
        "Lay out the phases for going from this paper to a working "
        "product. The first phase must be reproducing the paper's own "
        "baseline result; the last must be a deployed, usable product. "
        "Return JSON with a 'phases' list; each item has 'name', 'steps' "
        "(a list of concrete actions), 'definition_of_done' (one "
        "checkable sentence), and 'risks' (a list of what could block "
        "this phase, such as a dataset needing licence approval)."
    )


def _findings_block(findings: PaperFindings) -> str:
    """Render extracted findings as labelled lines for a prompt."""
    lines = []
    for field, values in findings.items():
        rendered = "; ".join(values) if values else "not stated in the paper"
        lines.append(f"- {field}: {rendered}")
    return "\n".join(lines)
