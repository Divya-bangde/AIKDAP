"""Asset ORM model — the universal knowledge object every uploaded file,
generated report, dataset, AI summary, chart, or workflow output is
stored as.

Note on the `metadata` column: the bare attribute name `metadata` is
reserved on SQLAlchemy's `DeclarativeBase` (it holds the `MetaData`
instance), so the Python attribute here is `asset_metadata` while the
underlying database column is still named `metadata`, matching the
domain field name. `schemas.AssetRead.from_model` bridges this back to
a `metadata` field in the public API.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import BaseModel
from app.modules.assets.enums import (
    AssetProcessingStatus,
    AssetSource,
    AssetStatus,
    AssetType,
)


def _enum_values(enum_cls: type) -> list[str]:
    """Persist enum members by their `.value`, not their `.name`.

    SQLAlchemy's `Enum` defaults to storing Python enum *names*
    (`RESEARCH`) rather than `.value`s (`research`) unless told
    otherwise; without this, the database enum would silently diverge
    from the lowercase wire values the API and spec use.
    """
    return [member.value for member in enum_cls]


class Asset(BaseModel):
    """A reusable unit of work — uploaded, generated, or imported — owned
    by a user within one of their projects."""

    __tablename__ = "assets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    asset_type: Mapped[AssetType] = mapped_column(
        SQLEnum(AssetType, name="asset_type_enum", native_enum=True, values_callable=_enum_values),
        nullable=False,
    )
    status: Mapped[AssetStatus] = mapped_column(
        SQLEnum(AssetStatus, name="asset_status_enum", native_enum=True, values_callable=_enum_values),
        nullable=False,
        default=AssetStatus.ACTIVE,
    )
    source: Mapped[AssetSource] = mapped_column(
        SQLEnum(AssetSource, name="asset_source_enum", native_enum=True, values_callable=_enum_values),
        nullable=False,
        default=AssetSource.UPLOAD,
    )

    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(50)), nullable=False, default=list)

    asset_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    ai_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Generated assets only: how much of the content came from project
    # documents, the web and general knowledge
    # (`research.source_mix.compute_report_source_mix`). Null for
    # uploads and for assets saved before it existed.
    source_mix: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Sprint 6: asset processing pipeline status tracking. Distinct from
    # `status` above (lifecycle, not pipeline progress).
    processing_status: Mapped[AssetProcessingStatus] = mapped_column(
        SQLEnum(
            AssetProcessingStatus,
            name="asset_processing_status_enum",
            native_enum=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=AssetProcessingStatus.PENDING,
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
