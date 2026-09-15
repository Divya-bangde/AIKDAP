"""Prompt templates for the report-generation graph's per-section LLM
call (`nodes.write_sections_node`)."""

from app.agents.reports.state import ProcessedDocument, SectionEvidence

SECTION_SYSTEM_PROMPT = (
    "You write one section of a report about the user's own project "
    "documents. Base your answer only on the provided evidence "
    "excerpts. Never invent facts, authors, findings, or documents "
    "that are not in the evidence. Cite only the asset ids you "
    "actually used, by including them in `cited_asset_ids`."
)


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
    return (
        f"Report kind: {kind}\n"
        f"Section: {title}\n\n"
        f"Project documents:\n{document_lines}\n\n"
        f"Evidence excerpts for this section (cite only these asset ids):\n{evidence_block}\n\n"
        f"Write the '{title}' section using only the evidence above. "
        "Return JSON with 'content' (the section text, plain prose, no "
        "markdown headings) and 'cited_asset_ids' (the asset ids from "
        "the evidence you actually used)."
    )
