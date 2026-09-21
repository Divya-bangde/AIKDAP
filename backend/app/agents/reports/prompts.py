"""Prompt templates for the report-generation graph's per-section LLM
call (`nodes.write_sections_node`)."""

from app.agents.reports.state import ProcessedDocument, SectionEvidence

SECTION_SYSTEM_PROMPT = (
    "You are an academic research analyst writing one section of a "
    "formal report about the user's own project documents.\n\n"
    "Rules:\n"
    "- Base every statement only on the provided evidence excerpts. "
    "Never invent facts, authors, findings, numbers, or documents that "
    "are not in the evidence.\n"
    "- Write in precise academic prose, in the indicative tense: 'The "
    "authors present...', 'The framework uses...'. Never describe "
    "published work as a speculative proposal ('This project aims "
    "to...').\n"
    "- Stay strictly inside the section's definition. Do not spill "
    "benchmark numbers, hardware details, or evaluation results into a "
    "section that does not ask for them, and do not restate material "
    "that belongs to another section.\n"
    "- Cite only the asset ids you actually used, by including them in "
    "`cited_asset_ids`."
)

#: What each section must cover, and what it must leave to the others.
#: Keeping the boundaries explicit is what stops independently written
#: sections from repeating each other -- they are written one LLM call
#: at a time and cannot see one another's text.
SECTION_GUIDANCE: dict[str, str] = {
    "Title": (
        "State the exact full academic title of the work, as the "
        "evidence gives it. Never use a file name or an arXiv id as the "
        "title. Output the title alone, with no commentary."
    ),
    "Abstract": (
        "A 120-180 word high-level summary covering, in order: the "
        "foundational problem, the proposed method or architecture, how "
        "it was evaluated, and the primary quantitative conclusion."
    ),
    "Introduction": (
        "The context and motivation only: the field, why the topic "
        "matters, and what the work sets out to address. Leave the "
        "specific technical bottleneck to Problem Statement, prior work "
        "to Literature Review, and any results out entirely."
    ),
    "Problem Statement": (
        "Two to three sentences defining the specific bottleneck or "
        "technical limitation that existing work failed to solve before "
        "this work. No solution, no method, no results."
    ),
    "Objectives": (
        "Three bullet points stating strictly what the authors set out "
        "to investigate: the core algorithmic or architectural target, "
        "the specific hypothesis or constraint to achieve, and the "
        "scope of the empirical validation. Never list final benchmark "
        "numbers or hardware details here."
    ),
    "Literature Review": (
        "One to two paragraphs on the baseline methods and prior "
        "approaches the evidence names, ending with the precise gap "
        "that necessitated this work. No mathematical formulations and "
        "no architecture steps -- those belong to Methodology."
    ),
    "Methodology": (
        "A 150-220 word technical breakdown: the network architecture "
        "and core backbone (layers, attention mechanisms, hidden "
        "dimensions, rank matrices); the mathematical formulation, "
        "defining key equations and variables (for example a low-rank "
        "decomposition W = W0 + B*A); and the loss functions, "
        "optimization objectives and training constraints. Exclude "
        "experimental scores, benchmark comparisons and dataset metrics."
    ),
    "Experimental Outcomes & Results": (
        "A 120-160 word summary of quantitative performance: the "
        "datasets, benchmarks and comparative baselines; concrete "
        "numerical metrics (accuracy, F1, BLEU, parameter-reduction "
        "ratios, memory savings); and the key analytical or ablation "
        "insights. Use only numbers that appear in the evidence."
    ),
    "Key Themes": (
        "The themes and topics that recur across the documents, and "
        "which documents carry each one."
    ),
    "Main Findings": (
        "Each document's own principal findings, attributed document by "
        "document rather than merged into one summary."
    ),
    "How the Documents Relate": (
        "How the documents agree, disagree, or build on one another. "
        "Do not restate each document's findings -- that is Main "
        "Findings' job."
    ),
}


def render_section_prompt(
    *,
    title: str,
    kind: str,
    evidence: list[SectionEvidence],
    documents: list[ProcessedDocument],
) -> str:
    """Build the prompt for one section's LLM call.

    `documents` (every processed document's AI-profile summary/topics)
    is included for context even when a given excerpt does not come
    from every document -- it is what lets "Main Findings" name each
    document's own contribution rather than only the one or two chunks
    retrieval happened to surface.
    """
    document_lines = "\n".join(
        f"- [{document['asset_id']}] {document['title']}: {document['summary'] or 'No summary available.'}"
        for document in documents
    )
    evidence_block = "\n\n".join(
        f"[{item['asset_id']}] {item['title']} ({item['file_name']}):\n{item['snippet']}"
        for item in evidence
    )
    # A section with no registered guidance falls back to its title
    # alone, the same tolerance `SECTION_QUERIES` has in
    # `nodes.retrieve_evidence_node`.
    guidance = SECTION_GUIDANCE.get(title, f"Write the '{title}' section.")
    return (
        f"Report kind: {kind}\n"
        f"Section: {title}\n\n"
        f"Project documents:\n{document_lines}\n\n"
        f"Evidence excerpts for this section (cite only these asset ids):\n{evidence_block}\n\n"
        f"What this section must contain:\n{guidance}\n\n"
        f"Write the '{title}' section using only the evidence above. "
        "Return JSON with 'content' (the section text, plain prose, no "
        "markdown headings) and 'cited_asset_ids' (the asset ids from "
        "the evidence you actually used)."
    )


CITATION_SYSTEM_PROMPT = (
    "You read bibliographic details off the opening page of a "
    "document. Report only what the text actually states. Leave a "
    "field as an empty string when the text does not state it -- never "
    "infer, complete, or guess an author, a year, or a venue."
)


def render_citation_prompt(*, file_name: str, front_matter: str) -> str:
    """Build the prompt that reads one document's citation fields.

    Fed the document's own opening text rather than retrieved excerpts:
    the title page is what carries authors and year, and it is exactly
    what semantic retrieval discards.
    """
    return (
        f"Opening text of {file_name}:\n{front_matter}\n\n"
        "Return JSON with 'authors' (every author named, reordered into "
        "APA form -- surname first, then initials, ampersand before the "
        "last, as in 'Hu, E., Shen, Y., & Wallis, P.'; reorder and "
        "abbreviate only the names actually printed, never add or drop "
        "one), 'year' (the four-digit publication year), "
        "'title' (the exact full title, never a file name or an arXiv "
        "id -- repair PDF extraction artifacts in it, joining words "
        "broken across lines into one word, and "
        "closing gaps left by small-caps such as 'L OW-R ANK' into "
        "'LOW-RANK', without rewording it), and 'venue' (the journal, "
        "conference, or preprint server, "
        "for example 'arXiv'). Use an empty string for anything the "
        "text does not state."
    )
