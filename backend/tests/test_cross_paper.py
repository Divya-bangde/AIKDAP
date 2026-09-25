import pytest
import uuid
from app.agents.planner.cross_paper import synthesize_cross_paper
from app.modules.research.schemas import (
    ResearchDocumentUnderstanding, 
    ResearchGoal, 
    ResearchGap, 
    GapClassification,
    SufficiencyStatus,
    ResearchCertainty,
    ComparisonRelationship,
    GapResolutionState,
)

@pytest.mark.live_ollama
@pytest.mark.asyncio
async def test_cross_paper_preserves_boundaries_and_resolves_gaps():
    primary = ResearchDocumentUnderstanding(
        title="Primary Paper",
        methodology="Uses CNN with 90% accuracy",
        missing_information=[
            ResearchGap(
                gap_type="implementation",
                classification=GapClassification.REQUIRED,
                description="Missing exact learning rate.",
                why_needed="To reproduce",
            )
        ],
        sufficiency=SufficiencyStatus.PARTIALLY_SUFFICIENT,
        sufficiency_reason="Missing params"
    )
    
    supporting = [
        {
            "id": uuid.uuid4(),
            "title": "Supporting Paper A",
            "methodology": "Uses CNN with 99% accuracy and learning rate 0.001."
        }
    ]
    
    goal = ResearchGoal(type="reproduce", description="Reproduce the results")
    
    result = await synthesize_cross_paper(primary, supporting, goal)
    
    # 1. Primary/Supporting Boundary Test (Phase 8)
    assert len(result.comparison_items) > 0
    assert result.comparison_items[0].primary_claim == "Uses CNN with 90% accuracy"
    assert "99%" in result.comparison_items[0].supporting_claims[0]
    
    # 2. Gap Resolution Test (Phase 15)
    assert len(result.gap_resolutions) == 1
    assert result.gap_resolutions[0].resolution_state == GapResolutionState.PARTIALLY_RESOLVED
    assert "Exact value used by primary paper" in str(result.gap_resolutions[0].unresolved_portion)
    
    # 3. Hypothesis Generation (Phase 16)
    # The reducer should have generated a hypothesis about the differing methodology
    assert len(result.hypotheses) > 0
    assert "may be worth investigating" in result.hypotheses[0].description
