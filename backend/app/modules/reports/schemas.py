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
