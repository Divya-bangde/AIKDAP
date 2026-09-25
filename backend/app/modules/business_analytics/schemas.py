"""Request/response models for business analytics."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.modules.assets.enums import AssetProcessingStatus
from app.modules.assets.models import Asset


class AnalysisCreate(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    sheet: str | None = Field(default=None, max_length=100)


class ColumnProfileRead(BaseModel):
    name: str
    kind: str
    dtype: str
    null_pct: float
    distinct: int
    min: Any = None
    max: Any = None
    samples: list[Any]


class DatasetProfileRead(BaseModel):
    row_count: int
    truncated: bool
    columns: list[ColumnProfileRead]


class AnalysisRead(BaseModel):
    id: uuid.UUID
    dataset_id: uuid.UUID
    question: str
    status: AssetProcessingStatus
    error: str | None
    plan: dict[str, Any] | None
    result: dict[str, Any] | None
    narrative: str | None
    unverified_numbers: list[str]
    created_at: datetime

    @classmethod
    def from_asset(cls, asset: Asset) -> "AnalysisRead":
        analysis = asset.asset_metadata.get("analysis", {})
        return cls(
            id=asset.id,
            dataset_id=analysis["dataset_id"],
            question=analysis["question"],
            status=asset.processing_status,
            error=asset.processing_error,
            plan=analysis.get("plan"),
            result=analysis.get("result"),
            narrative=analysis.get("narrative"),
            unverified_numbers=analysis.get("unverified_numbers", []),
            created_at=asset.created_at,
        )


class KaggleFileRead(BaseModel):
    name: str
    size: int


class KaggleImportRequest(BaseModel):
    owner: str = Field(pattern=r"^[A-Za-z0-9._-]+$", max_length=100)
    dataset: str = Field(pattern=r"^[A-Za-z0-9._-]+$", max_length=100)
    file_name: str = Field(min_length=1, max_length=255)


class KaggleImportAccepted(BaseModel):
    status: str = "queued"
