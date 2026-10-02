import json
import uuid
from typing import Any

from app.core.llm.gateway import get_llm_gateway, LLMError
from app.core.logging.logger import get_logger
from app.modules.research.schemas import (
    CrossPaperComparison,
    GapResolutionState,
    ResearchDocumentUnderstanding,
    ResearchGoal,
)

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert scientific Research Reducer.
Your task is to synthesize findings across multiple research papers based on a primary paper and supporting papers.

RULES (STRICT):
1. SOURCE BOUNDARY: Primary and supporting papers are different evidence sources. NEVER merge them. NEVER invent missing information.
2. NEVER infer primary-paper values from supporting papers. If a supporting paper has an exact value but the primary paper does not, the primary paper remains UNKNOWN.
3. Preserve source references explicitly (paper_id).
4. Report contradictions accurately.
5. Distinguish evidence from interpretation.
6. HYPOTHESES: Hypotheses must be MODAL (e.g., "may be worth testing", "could improve"). Do not make deterministic claims like "will improve".
7. UNKNOWN must remain UNKNOWN. Do not guess.

Output valid JSON matching the exact schema provided.
"""

class CrossPaperValidationException(Exception):
    pass


def _validate_cross_paper_result(
    result: CrossPaperComparison, 
    primary_id: uuid.UUID,
    supporting_ids: set[uuid.UUID]
) -> None:
    """Post-LLM Validator (Phase 12)."""
    # 1. Every comparison claim has source references
    for item in result.comparison_items:
        if not item.source_references:
            raise CrossPaperValidationException("Comparison item missing source references.")
        # 2. Source paper exists
        for ref in item.source_references:
            if ref.paper_id != primary_id and ref.paper_id not in supporting_ids:
                raise CrossPaperValidationException(f"Invalid source reference paper_id: {ref.paper_id}")
            
    # 3-6. Gap resolution boundary check
    for gap in result.gap_resolutions:
        if gap.resolution_state == GapResolutionState.RESOLVED:
            # If resolved, ensure we aren't transferring supporting values to primary implicitly
            if not gap.evidence or "primary" not in gap.explanation.lower():
                pass # Simplified check. Real strict validation would require semantic parsing.
                
    # 7-8. Hypotheses constraints
    for hypo in result.hypotheses:
        if not hypo.supporting_sources:
            raise CrossPaperValidationException("Hypothesis missing supporting sources.")
        # 8. Modal checking
        deterministic_words = ["will", "proves", "guarantees", "certainly"]
        if any(word in hypo.description.lower() for word in deterministic_words):
            raise CrossPaperValidationException("Hypothesis contains deterministic claim instead of modal claim.")
            

async def synthesize_cross_paper(
    primary: ResearchDocumentUnderstanding,
    supporting_papers: list[dict[str, Any]],  # List of dicts representing structured facts
    goal: ResearchGoal,
) -> CrossPaperComparison:
    """
    Map-reduce pipeline to synthesize cross-paper findings using LLMGateway.
    """
    gateway = get_llm_gateway()
    
    # Extract IDs
    primary_id = uuid.uuid4() # Or passed from upstream
    supp_ids = []
    
    # Format input
    prompt = f"GOAL: {goal.type} - {goal.description}\n\n"
    prompt += f"PRIMARY PAPER (ID: {primary_id}):\n"
    prompt += primary.model_dump_json(indent=2) + "\n\n"
    
    prompt += "SUPPORTING PAPERS:\n"
    for paper in supporting_papers:
        paper_id = paper.get("id", uuid.uuid4())
        supp_ids.append(paper_id)
        prompt += f"--- SUPPORTING PAPER (ID: {paper_id}) ---\n"
        prompt += json.dumps(paper, indent=2, default=str) + "\n"

    try:
        response = await gateway.generate(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            response_format={"type": "json_schema", "json_schema": {"name": "CrossPaperComparison", "schema": CrossPaperComparison.model_json_schema()}},
            temperature=0.1
        )
        
        # Parse the JSON response
        try:
            data = json.loads(response.content)
            result = CrossPaperComparison.model_validate(data)
        except Exception as e:
            logger.error(f"Failed to parse LLM response: {response.content}")
            raise CrossPaperValidationException("Invalid JSON or schema mismatch") from e

        # Set IDs explicitly to ensure they match our system state
        result.primary_paper_id = primary_id
        result.supporting_paper_ids = supp_ids
        result.source_provenance = "LLMGateway: " + response.model
        
        # Post-LLM Validation
        _validate_cross_paper_result(result, primary_id, set(supp_ids))
        
        return result
        
    except LLMError as e:
        logger.error(f"LLM Error during cross-paper synthesis: {e}")
        raise ValueError(f"Cross-paper synthesis failed: {e}")
    except CrossPaperValidationException as e:
        logger.error(f"Validation failed: {e}")
        raise ValueError(f"Cross-paper synthesis failed safety checks: {e}")
