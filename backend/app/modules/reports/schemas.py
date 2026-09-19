"""Pydantic v2 request/response schemas for the reports module."""

import enum
import uuid

from pydantic import BaseModel, Field

from app.modules.assets.enums import AssetProcessingStatus
from app.modules.assets.models import Asset
from app.modules.assets.schemas import AssetRead
from app.modules.research.models import ResearchStep
from app.modules.research.schemas import ResearchStepRead


class ReportKind(str, enum.Enum):
    """Which synopsis to generate (spec section 5). `modules/reports` is
    shared with the future build-plan feature (spec section 6); this
    enum stays scoped to synopsis kinds only -- a build plan is not a
    `ReportKind`, it is its own endpoint."""

    STUDY_SUMMARY = "study_summary"
    PROJECT_SYNOPSIS = "project_synopsis"


class ReportGenerateRequest(BaseModel):
    """Payload for `POST /projects/{project_id}/reports/synopsis`."""

    kind: ReportKind


class ReportGenerationAccepted(BaseModel):
    """202 response: the report asset id and its initial status, so the
    caller can start polling `GET /assets/{asset_id}` immediately."""

    asset_id: uuid.UUID
    status: AssetProcessingStatus


#: The `asset_metadata["kind"]` marker for a build plan. Deliberately a
#: plain string rather than a `ReportKind` member (design ruling R1): a
#: build plan is its own endpoint with its own graph, and widening
#: `ReportKind` would make it a valid body for the synopsis endpoint.
BUILD_PLAN_KIND = "build_plan"


class BuildPlanRequest(BaseModel):
    """Payload for `POST /projects/{project_id}/reports/build-plan`.

    `min_length=1` rejects an empty selection with FastAPI's own `422`
    before any handler runs -- the spec requires at least one paper, and
    an empty list must never be read as "all documents".
    """

    asset_ids: list[uuid.UUID] = Field(min_length=1)


class ReportRead(AssetRead):
    """`GET /reports/{asset_id}`: the report asset plus its persisted
    step trace (hardening item 1), so the frontend renders a report run
    with the same pipeline view a research run uses. A separate report
    endpoint, rather than widening `AssetRead`, keeps the assets module
    unaware of the research step table."""

    steps: list[ResearchStepRead] = Field(default_factory=list)

    @classmethod
    def from_report(cls, asset: Asset, steps: list[ResearchStep]) -> "ReportRead":
        return cls(
            **AssetRead.from_model(asset).model_dump(),
            steps=[ResearchStepRead.model_validate(step) for step in steps],
        )
