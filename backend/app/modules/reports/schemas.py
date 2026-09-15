"""Pydantic v2 request/response schemas for the reports module."""

import enum
import uuid

from pydantic import BaseModel

from app.modules.assets.enums import AssetProcessingStatus


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
