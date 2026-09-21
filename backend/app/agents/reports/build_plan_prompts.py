"""Prompt templates for the build-plan graph's four LLM calls
(Milestone 10 step 5 -- spec section 6).

The voice is a principal AI/ML systems architect turning a paper into a
production build, not a reviewer summarising one: every call is steered
towards engineering viability, operational cost and deployment shape.
The JSON contract each call returns is unchanged -- `BuildPlanState`'s
`PaperFindings`, `ToolRecommendation` and `BuildPhase` keys are frozen,
so the architect framing has to fit inside them rather than add fields.

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
#: prompts cannot drift on the rule that matters most. Checkpoint and
#: package names are called out because the architect framing asks for
#: them by name, and "huggingface.co/org/model" would be stripped to
#: nothing while "org/model" survives.
_NO_LINKS_RULE = (
    "Never write a URL, link, DOI, or web address of any kind -- not in "
    "prose, not in parentheses, not as a citation. Name checkpoints and "
    "packages by their bare identifier, such as 'org/model-name', never "
    "as a web address. Links are added separately from verified search "
    "results. Any URL you write will be deleted before the reader sees it."
)

EXTRACT_SYSTEM_PROMPT = (
    "You are a principal AI/ML systems architect reading a paper the "
    "user wants to ship as a production system. You report only what the "
    "paper itself states, but you read it for engineering viability: "
    "which mechanism actually has to run in production, which weights "
    "can be loaded instead of trained, what the compute bill looks like. "
    "Base every field strictly on the provided excerpts. If the excerpts "
    "do not state something, return an empty list for that field rather "
    "than guessing a plausible value. " + _NO_LINKS_RULE
)

TOOLS_SYSTEM_PROMPT = (
    "You are a principal AI/ML systems architect choosing the production "
    "stack. For each stage you prescribe exactly one primary tool and "
    "justify it on performance and maintainability, referring to "
    "something specific in the paper's findings -- a named model, "
    "dataset, metric, index size, or compute need. Name real, "
    "currently-maintained tools only. List the credible alternatives so "
    "the builder is not locked in, but commit to one. " + _NO_LINKS_RULE
)

PROCESS_SYSTEM_PROMPT = (
    "You are a principal AI/ML systems architect writing a phased "
    "engineering roadmap: environment and ingestion, then the indexing "
    "and inference core, then the production API, then benchmarking and "
    "containerisation. Each phase gets concrete deliverables (named "
    "scripts, services, configs), the command an engineer runs to verify "
    "them locally, one quantitative definition of done, and the "
    "real-world failure modes that phase carries -- each paired with its "
    "engineering mitigation. Prefer numbers over adjectives: latency "
    "percentiles, memory ceilings, throughput, licence gates. "
    + _NO_LINKS_RULE
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
        "From these papers only, distil what would actually have to be "
        "built and run in production. Separate the production-critical "
        "core from the academic apparatus around it. Return JSON with "
        "these keys, each a list of short statements:\n"
        "- 'method': the mechanisms, algorithms, or data structures that "
        "must exist at serving time -- the parts a production system "
        "cannot omit.\n"
        "- 'models': the models or architectures used, giving the exact "
        "pretrained checkpoint identifier wherever the paper names one, "
        "so weights can be loaded rather than trained.\n"
        "- 'datasets': the datasets used, noting any licence or access "
        "restriction the paper mentions.\n"
        "- 'metrics': the evaluation metrics, with the paper's own "
        "reported numbers where stated, so a reimplementation has a "
        "target to hit.\n"
        "- 'compute': hardware, GPU, VRAM, index size, or training-time "
        "needs exactly as stated.\n"
        "- 'limitations': the limitations the authors themselves state, "
        "plus any part of the work that exists only to satisfy the "
        "paper's evaluation and can be left out of a production build.\n"
        "Use an empty list for anything the excerpts do not state."
    )


def render_tools_prompt(*, findings: PaperFindings, links: list[ResearchLink]) -> str:
    """Prompt for the per-stage tool recommendations.

    `links` are passed as titles and notes only -- never URLs -- so the
    model can take a real implementation or library into account without
    ever being handed a URL it might echo back.

    The stage list is spelled out with the production tier each one
    stands for, because `TOOL_STAGES` values are single words and a
    model asked for "backend" tools will otherwise skip the serving
    runtime and the index engine.
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
        "Prescribe the production stack for building this system, one "
        f"primary tool per tier. Use exactly these stage values: {stages}. "
        "The tiers mean:\n"
        "- 'data': ingestion, preprocessing, and orchestration of the "
        "corpus, including serialisation format and job queue.\n"
        "- 'modelling': the core ML framework and anything that loads or "
        "quantises the pretrained weights.\n"
        "- 'backend': the serving and inference runtime, the vector or "
        "inverted index engine, and the caching layer behind the API.\n"
        "- 'evaluation': benchmarking, load testing, and the "
        "observability stack that proves the metrics in production.\n"
        "- 'deployment': containerisation, hosting target, and the "
        "hardware class it runs on.\n\n"
        "Return JSON with a 'tools' list; each item has 'stage' (one of "
        "the stage values), 'name' (the one tool you prescribe), 'reason' "
        "(why, on performance and maintainability grounds, referring to a "
        "specific finding above), and 'alternatives' (a list of other "
        "tools that would also work). Cover every stage that the findings "
        "support, and give exactly one primary tool per stage."
    )


def render_process_prompt(*, findings: PaperFindings, tools: list[ToolRecommendation]) -> str:
    """Prompt for the phased build process.

    The four phase names are fixed here rather than left to the model so
    every build plan reads the same way, and the two things the frozen
    five-section document has no home for -- the component flow and the
    hardware and cost sizing -- are pinned to the phases where an
    engineer would actually need them.
    """
    findings_block = _findings_block(findings)
    tool_lines = (
        "\n".join(f"- {tool['stage']}: {tool['name']} ({tool['reason']})" for tool in tools)
        or "No tools were recommended."
    )
    return (
        f"Findings from the paper:\n{findings_block}\n\n"
        f"Recommended tools:\n{tool_lines}\n\n"
        "Lay out the engineering roadmap from this paper to a deployed "
        "product, as exactly four phases in this order:\n"
        "1. Environment, pretrained weights, and offline ingestion "
        "pipeline. Its steps must open with the end-to-end component "
        "flow -- ingestion, processing and indexing, storage, query "
        "serving API, client -- naming the serialisation format moving "
        "between each pair.\n"
        "2. Core indexing engine and hardware acceleration: quantisation, "
        "inference runtime, and the index build itself.\n"
        "3. Production API and asynchronous service layer.\n"
        "4. Benchmarking, containerisation, and stress testing. Its steps "
        "must include the target production sizing -- CPU cores, RAM, "
        "VRAM, and disk per 100k data units -- and an estimated monthly "
        "cloud hosting cost for that hardware class.\n\n"
        "Return JSON with a 'phases' list; each item has 'name', 'steps' "
        "(concrete deliverables -- named scripts, services, and configs -- "
        "with the exact command an engineer runs to verify the phase "
        "locally), 'definition_of_done' (one checkable sentence with a "
        "number in it, such as a P95 latency ceiling under a stated "
        "concurrency, an index build time, or a metric the paper "
        "reported), and 'risks' (the real-world failure modes this phase "
        "carries -- out-of-memory spikes during index building, cold "
        "start latency, query token explosion, cache invalidation, a "
        "dataset needing licence approval -- each stated as the failure "
        "followed by its engineering mitigation)."
    )


def _findings_block(findings: PaperFindings) -> str:
    """Render extracted findings as labelled lines for a prompt."""
    lines = []
    for field, values in findings.items():
        rendered = "; ".join(values) if values else "not stated in the paper"
        lines.append(f"- {field}: {rendered}")
    return "\n".join(lines)
