"""Safe LLM Query Understanding & Reformulation.

Implements the validated query reformulation architecture from Sprint 15 Phase 2/3.
"""

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from app.core.llm.gateway import LLMGateway
from app.core.logging.logger import get_logger

logger = get_logger(__name__)


class QueryReformulation(BaseModel):
    """Structured output for query understanding and reformulation."""

    search_query: str = Field(
        ...,
        description="The optimized search query for dense retrieval. Must preserve all constraints.",
    )
    intent: str = Field(
        ..., description="The intent of the query (e.g., question, command, statement)."
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Explicitly named organizations, people, or places.",
    )
    metrics: list[str] = Field(
        default_factory=list,
        description="Explicitly named metrics (e.g., revenue, production volume).",
    )
    dates: list[str] = Field(
        default_factory=list, description="Explicit dates, years, or quarters."
    )
    regions: list[str] = Field(
        default_factory=list, description="Explicitly named regions or locations."
    )
    products: list[str] = Field(
        default_factory=list, description="Explicitly named products or services."
    )
    numbers: list[str] = Field(
        default_factory=list, description="Explicit numbers or thresholds."
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Explicit constraints or comparisons (e.g., > 10M, before 2026).",
    )
    negations: list[str] = Field(
        default_factory=list,
        description="Explicit negative constraints (e.g., did not meet target, excluding).",
    )
    context_resolutions: list[str] = Field(
        default_factory=list,
        description="Pronouns or terms successfully resolved from explicit context.",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Pronouns or terms that cannot be resolved safely.",
    )
    unsupported_assumptions: list[str] = Field(
        default_factory=list, description="Any guesses or inferred entities."
    )


def is_reformulation_safe(
    original: str, structured: QueryReformulation, workspace_context: dict[str, Any] | None = None
) -> tuple[bool, str]:
    """Deterministically validate that a reformulation preserves constraints."""
    original_lower = original.lower()

    # 1. Ambiguity or Assumptions
    if structured.ambiguities:
        return False, f"Ambiguous terms detected: {structured.ambiguities}"
    if structured.unsupported_assumptions:
        return False, f"Unsupported assumptions detected: {structured.unsupported_assumptions}"

    # 2. Entity/Number/Date preservation check
    def check_hallucination(items: list[str], category: str, allow_from_context: bool = False) -> tuple[bool, str]:
        for item in items:
            item_lower = item.lower()
            if item_lower in original_lower:
                continue
            
            # Check context if allowed
            if allow_from_context and workspace_context:
                context_vals = [str(v).lower() for v in workspace_context.values() if v]
                if any(item_lower in cv or cv in item_lower for cv in context_vals):
                    continue
            
            return False, f"Hallucinated {category}: {item}"
        return True, ""

    # Entities can be derived from context
    safe, reason = check_hallucination(structured.entities, "entity", allow_from_context=True)
    if not safe:
        return safe, reason
    safe, reason = check_hallucination(structured.dates, "date")
    if not safe:
        return safe, reason
    safe, reason = check_hallucination(structured.regions, "region")
    if not safe:
        return safe, reason
    safe, reason = check_hallucination(structured.products, "product")
    if not safe:
        return safe, reason
    safe, reason = check_hallucination(structured.numbers, "number")
    if not safe:
        return safe, reason

    # 3. Negation preservation
    negation_terms = ["not", "n't", "no", "never", "without", "exclude", "excluding"]
    has_negation = any(term in original_lower for term in negation_terms)
    
    if has_negation:
        if not structured.negations:
            return False, "Dropped negation constraint (missing in structured output)"
        
        sq_lower = structured.search_query.lower()
        if not any(term in sq_lower for term in negation_terms) and not structured.negations:
            return False, "Dropped negation constraint (missing in search query)"

    # 4. Comparison preservation
    comparison_terms = [">", "<", ">=", "<=", "equal to", "before", "after", "higher than", "lower than", "above", "below", "more than", "less than"]
    for term in comparison_terms:
        if term in original_lower:
            sq_lower = structured.search_query.lower()
            str_constraints = " ".join(structured.constraints).lower()
            if term not in sq_lower and term not in str_constraints:
                if term in [">", "<", ">=", "<="]:
                     return False, f"Dropped comparison operator: {term}"
                return False, f"Dropped comparison constraint: {term}"
                
    # 5. Number preservation
    nums = re.findall(r'\b\d+\b', original_lower)
    for n in nums:
        sq_lower = structured.search_query.lower()
        str_nums = " ".join(structured.numbers).lower()
        if n not in sq_lower and n not in str_nums:
            return False, f"Dropped numerical constraint: {n}"

    return True, "SAFE"


REFORMULATOR_PROMPT = """You are a safe query understanding and reformulation engine for an enterprise retrieval system.
Your job is to transform natural language questions into optimized retrieval queries.

CRITICAL SAFETY RULES:
1. You are generating a retrieval query, not answering the user.
2. DO NOT add facts or guess entities.
3. DO NOT infer unsupported numbers, dates, or regions.
4. DO NOT change comparison operators (e.g. > must remain >).
5. DO NOT remove negations.
6. DO NOT change the requested metric.
7. DO NOT narrow the question without evidence.
8. PRESERVE all explicit user constraints.
9. If a pronoun (they, it, them, this company) is used, resolve it using the `WORKSPACE_CONTEXT` ONLY if the context unambiguously provides the answer. Record this in `context_resolutions`. If uncertain, mark it in `ambiguities` and DO NOT guess.
10. Return the exact constraints extracted in the requested JSON fields.

WORKSPACE_CONTEXT (Explicit UI State):
{context_json}

You must output a valid JSON object matching this schema:
{schema}
"""

async def reformulate_query(
    original_query: str,
    gateway: LLMGateway,
    workspace_context: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """
    Safely reformulate a query using the LLMGateway and deterministic validation.
    Returns (retrieval_query, metadata).
    """
    try:
        schema_json = json.dumps(QueryReformulation.model_json_schema(), indent=2)
        context_json = json.dumps(workspace_context, indent=2) if workspace_context else "No active context."
        sys_prompt = REFORMULATOR_PROMPT.replace("{schema}", schema_json).replace("{context_json}", context_json)
        
        response = await gateway.generate(
            prompt=original_query,
            system_prompt=sys_prompt,
            response_format={"type": "json_object"},
            temperature=0.0,
            allow_fallback=True
        )
        
        raw_json = response.content
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:-3].strip()
        elif raw_json.startswith("```"):
            raw_json = raw_json[3:-3].strip()
            
        structured = QueryReformulation.model_validate_json(raw_json)
        
        safe, reason = is_reformulation_safe(original_query, structured, workspace_context)
        
        metadata = {
            "reformulation_attempted": True,
            "reformulation_accepted": safe,
            "rejection_reason": reason if not safe else None,
            "provider_used": response.provider,
            "model_used": response.model,
            "latency_ms": response.latency_ms,
            "structured_output": structured.model_dump(),
        }
        
        if safe:
            logger.info(
                "query_reformulation_accepted",
                original=original_query,
                retrieval=structured.search_query,
                latency_ms=response.latency_ms
            )
            return structured.search_query, metadata
        else:
            logger.warning(
                "query_reformulation_rejected",
                reason=reason,
                original=original_query,
                retrieval=structured.search_query,
                latency_ms=response.latency_ms
            )
            return original_query, metadata
            
    except Exception as e:
        logger.error(
            "query_reformulation_failed",
            error=str(e),
            error_type=type(e).__name__,
            original=original_query
        )
        return original_query, {
            "reformulation_attempted": True,
            "reformulation_accepted": False,
            "rejection_reason": f"Exception: {type(e).__name__}",
        }
