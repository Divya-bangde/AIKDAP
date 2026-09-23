"""Pydantic v2 request/response schemas for the assets module."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.assets.ai_profile import AIProfile
from app.modules.assets.enums import AssetProcessingStatus, AssetSource, AssetStatus, AssetType
from app.modules.assets.models import Asset
from app.modules.research.source_mix import SourceMix


class AssetUpdate(BaseModel):
    """Payload for partially updating an asset's metadata (not its file).

    Unset fields are left untouched; explicit `null` clears a field.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    status: AssetStatus | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None


class AssetRead(BaseModel):
    """Public representation of an asset.

    `storage_path` is intentionally omitted — clients retrieve file
    bytes via `GET /assets/download/{id}`, never a raw filesystem or
    bucket path.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str | None
    asset_type: AssetType
    status: AssetStatus
    mime_type: str
    file_name: str
    file_extension: str
    file_size: int
    checksum: str
    source: AssetSource
    version: int
    tags: list[str]
    metadata: dict[str, Any]
    ai_profile: AIProfile
    #: Where a generated report's support came from. `None` for uploads
    #: and for reports saved before this was computed.
    source_mix: SourceMix | None = None
    created_by: uuid.UUID | None
    processing_status: AssetProcessingStatus
    processing_error: str | None
    processing_started_at: datetime | None
    processing_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, asset: Asset) -> "AssetRead":
        """Map ORM columns to API fields.

        Needed because the ORM attribute is `asset_metadata` (the bare
        name `metadata` is reserved by SQLAlchemy's Declarative Base),
        while the public API field is `metadata` per the domain spec.
        """
        return cls(
            id=asset.id,
            project_id=asset.project_id,
            owner_id=asset.owner_id,
            title=asset.title,
            description=asset.description,
            asset_type=asset.asset_type,
            status=asset.status,
            mime_type=asset.mime_type,
            file_name=asset.file_name,
            file_extension=asset.file_extension,
            file_size=asset.file_size,
            checksum=asset.checksum,
            source=asset.source,
            version=asset.version,
            tags=list(asset.tags or []),
            metadata=asset.asset_metadata or {},
            ai_profile=asset.ai_profile or {},
            source_mix=asset.source_mix,
            created_by=asset.created_by,
            processing_status=asset.processing_status,
            processing_error=asset.processing_error,
            processing_started_at=asset.processing_started_at,
            processing_completed_at=asset.processing_completed_at,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
