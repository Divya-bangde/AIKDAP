import pytest
import uuid
from app.agents.planner.cross_paper import synthesize_cross_paper
from app.modules.research.schemas import (
    ResearchDocumentUnderstanding, 
    ResearchGoal, 
    ResearchGap, 
    GapClassification,
    SufficiencyStatus,
    GapResolutionState,
    DocumentPassage,
)

@pytest.mark.live_ollama
@pytest.mark.asyncio
async def test_cross_paper_preserves_boundaries_and_resolves_gaps():
    primary_id, supporting_id = uuid.uuid4(), uuid.uuid4()
    primary = ResearchDocumentUnderstanding(
        asset_id=primary_id,
        source_passages=[DocumentPassage(asset_id=primary_id, chunk_id=uuid.uuid4(), chunk_index=0,
            content="Uses CNN with 90% accuracy")],
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
        ResearchDocumentUnderstanding(
            asset_id=supporting_id,
            title="Supporting Paper A",
            methodology="Uses CNN with 99% accuracy and learning rate 0.001.",
            source_passages=[DocumentPassage(asset_id=supporting_id, chunk_id=uuid.uuid4(), chunk_index=0,
                content="Uses CNN with 99% accuracy and learning rate 0.001.")],
            sufficiency=SufficiencyStatus.SUFFICIENT, sufficiency_reason="Parameters present",
        )
    ]
    
    goal = ResearchGoal(type="reproduce", description="Reproduce the results")
    
    result = await synthesize_cross_paper(primary, supporting, goal)
    
    # 1. Primary/Supporting Boundary Test (Phase 8)
    assert len(result.comparison_items) > 0
    assert "90%" in result.comparison_items[0].primary_claim
    assert "99%" in result.comparison_items[0].supporting_claims[0]
    
    # 2. Gap Resolution Test (Phase 15)
    assert all(g.resolution_state != GapResolutionState.RESOLVED for g in result.gap_resolutions)
    
    # 3. Hypothesis Generation (Phase 16)
    # The reducer should have generated a hypothesis about the differing methodology
    assert result.primary_paper_id == primary_id
    assert result.supporting_paper_ids == [supporting_id]
    assert all(ref.chunk_id is not None for item in result.comparison_items for ref in item.source_references)
