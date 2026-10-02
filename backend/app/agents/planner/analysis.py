"""Research document analysis pipeline (Sprint 16 Phase 1).

Performs a full-document read to construct a structured `ResearchDocumentUnderstanding`
using the LLM Gateway.
"""

import json
import uuid

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm.gateway import LLMGateway, LLMMessage
from app.modules.assets.repository import AssetRepository
from app.modules.knowledge_base.repository import KnowledgeChunkRepository
from app.modules.research.schemas import (
    AnalyzeDocumentRequest,
    ResearchDocumentUnderstanding,
)

SYSTEM_PROMPT = """You are a rigorous, highly literal Research Intelligence AI.
Your task is to analyze the provided research paper (supplied as a series of text chunks) and extract a highly structured `ResearchDocumentUnderstanding` JSON.

CRITICAL INSTRUCTIONS:
1. EXPLICITLY PRESENT vs INFERRED: You must NEVER invent or hallucinate data. If a learning rate, dataset size, or metric is not explicitly stated in the text, you MUST leave it null or omit it. Do not fill in missing information from your own model knowledge.
2. SOURCE EVIDENCE: For equations and variables, you must trace them to their source location (e.g., "Chunk 4" or the stated page/section if available).
3. RESEARCH GAPS: You will be given a "User Goal". You must identify what information is missing from the paper that is necessary to achieve that goal. Classify each gap as REQUIRED, HELPFUL, OPTIONAL, or AMBIGUOUS.
4. CONFLICTS: If the paper contradicts itself (e.g., Table 1 says 92%, abstract says 89%), explicitly log this in the `conflicts` array.
5. SUFFICIENCY: Assess if the paper is SUFFICIENT, PARTIALLY_SUFFICIENT, INSUFFICIENT, or AMBIGUOUS for the user's stated goal, and explain why in `sufficiency_reason`.

You must respond ONLY with valid JSON matching the ResearchDocumentUnderstanding schema."""

async def analyze_research_document(
    asset_id: uuid.UUID,
    project_id: uuid.UUID,
    request: AnalyzeDocumentRequest,
    session: AsyncSession,
) -> ResearchDocumentUnderstanding:
    """Analyze a single research document against a user goal."""
    asset_repo = AssetRepository(session)
    chunk_repo = KnowledgeChunkRepository(session)
    
    asset = await asset_repo.get_by_id(asset_id)
    if not asset or asset.project_id != project_id:
        raise ValueError("Asset not found or access denied.")

    # Retrieve all chunks for this asset.
    # A limit of 10,000 is safely above any single document's chunk count in this system.
    chunks = await chunk_repo.list_by_project(project_id, asset_id=asset_id, limit=10000)
    
    if not chunks:
        raise ValueError("No extracted content available for this asset.")

    # Assemble the document.
    document_text = "\n\n".join(
        f"--- Chunk {i+1} (ID: {c.id}) ---\n{c.content}"
        for i, c in enumerate(chunks)
    )

    # Context injection
    context_str = ""
    if request.workspace_context:
        context_str = f"\n\nWorkspace Context:\n{request.workspace_context.model_dump_json(indent=2)}"

    user_prompt = (
        f"User Goal: {request.goal.type} - {request.goal.description}{context_str}\n\n"
        f"Document Content:\n{document_text}"
    )

    gateway = LLMGateway()
    messages = [
        LLMMessage(role="system", content=SYSTEM_PROMPT),
        LLMMessage(role="user", content=user_prompt)
    ]

    response = await gateway.complete(
        messages=messages,
        # Sprint 16 Phase 8.2: plain `json_object` mode let the model invent
        # its own (richer, differently-shaped) structure against a real
        # paper -- e.g. a nested `methodology` object instead of the
        # required string, `sufficiency`/`sufficiency_reason` omitted
        # entirely -- failing Pydantic validation every time. `cross_paper.py`
        # already solves this the same way for `CrossPaperComparison`;
        # applying the same existing pattern here, not a new mechanism.
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "ResearchDocumentUnderstanding",
                "schema": ResearchDocumentUnderstanding.model_json_schema(),
            },
        },
        temperature=0.0,
        # Sprint 16 Phase 8.2: the default (`settings.llm_max_tokens`, 2048)
        # silently truncated the model's JSON mid-object on a real 30-page
        # paper -- this was never exercised against real chunk content
        # before the `c.text_content` bug fix, so the cutoff was never
        # observed. Raised to give a full-document extraction room to
        # finish; still finite, not a workaround for a specific paper.
        max_tokens=8192,
    )

    # Sprint 16 Phase 8.3: a truncated response is not a parse failure to
    # discover by feeding half a JSON object to `json.loads` -- the
    # gateway already knows, via `finish_reason`, that generation was cut
    # off before the model could finish. Caught here, distinctly, before
    # the parse step has a chance to raise a confusing `JSONDecodeError`
    # for what is really a max_tokens problem.
    if response.finish_reason == "length":
        raise ValueError(
            "The model's response was truncated before it finished "
            "(finish_reason=length) -- the document or the requested "
            "extraction is too large for the current token budget."
        )

    content = response.content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.endswith("```"):
        content = content[:-3]

    # Sprint 16 Phase 8.3: previously a single `except Exception` wrapped
    # both failure kinds in one `RuntimeError`, which nothing upstream
    # catches -- every real failure surfaced as an opaque HTTP 500.
    # `ValueError` is this function's existing, already-handled error
    # contract (see the two raises above, and
    # `research/router.py::analyze_research_document_route`, which maps
    # `ValueError` to `400`) -- reused here, not a new exception type,
    # with the two real failure kinds kept distinguishable by message.
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"The model's response was not valid JSON: {e}\nRaw output: {content}"
        ) from e

    try:
        understanding = ResearchDocumentUnderstanding.model_validate(parsed)
    except ValidationError as e:
        raise ValueError(
            f"The model's response did not match the expected schema: {e}"
        ) from e

    # Persist transiently in the asset metadata
    metadata = asset.asset_metadata.copy() if asset.asset_metadata else {}
    metadata["research_understanding"] = understanding.model_dump(mode="json")
    asset.asset_metadata = metadata
    
    # We don't commit here; the caller (Service) handles transaction boundaries.
    return understanding
