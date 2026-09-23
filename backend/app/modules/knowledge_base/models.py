"""KnowledgeChunk ORM model — the atomic unit of the knowledge base.

Each chunk is a slice of text extracted and split from one asset.

`embedding` is a native pgvector column (Sprint 9C), replacing the
JSONB placeholder Sprint 6 originally shipped here — that column's own
docstring said adding a real embedding provider meant "adding the
pgvector extension and migrating this column then," and no chunk in
any environment had ever had a non-null value in it (verified before
migrating), so this is that migration, not a second representation
living alongside the first.
"""

import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.database.base import BaseModel
from app.modules.assets.enums import EmbeddingStatus
from app.modules.knowledge_base.enums import EmbeddingProviderName


def _enum_values(enum_cls: type) -> list[str]:
    """Persist enum members by their `.value`, not their `.name` (see
    the identical helper in `app.modules.assets.models` for why)."""
    return [member.value for member in enum_cls]


class KnowledgeChunk(BaseModel):
    """A chunk of extracted text from an asset, pending (future) embedding."""

    __tablename__ = "knowledge_chunks"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Sprint 12.1: provenance from the extracted unit this chunk came
    # from. All nullable and format-dependent — a PDF chunk has
    # `page_number`, an XLSX chunk has `sheet_name`, a plain-text chunk
    # has none of the three. Never more than one of `page_number`/
    # `sheet_name` is meaningful for a given chunk; `section` (a
    # heading, slide title) can accompany either or stand alone.
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    section: Mapped[str | None] = mapped_column(String(500), nullable=True)

    embedding_status: Mapped[EmbeddingStatus] = mapped_column(
        SQLEnum(
            EmbeddingStatus,
            name="kb_embedding_status_enum",
            native_enum=True,
            values_callable=_enum_values,
        ),
        nullable=False,
        default=EmbeddingStatus.PENDING,
    )
    embedding_provider: Mapped[EmbeddingProviderName | None] = mapped_column(
        SQLEnum(
            EmbeddingProviderName,
            name="kb_embedding_provider_enum",
            native_enum=True,
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dimension), nullable=True
    )


class ChunkPosition(BaseModel):
    """Where one PDF chunk's text sits on its page (click-to-source).

    Kept in its own table rather than on `knowledge_chunks`: it is
    nested, display-only data written after chunking, and a chunk from
    any non-PDF format simply has no row. Cascades with its chunk, so
    reprocessing an asset (which replaces its chunks) replaces these
    too. See `assets.processing.positions` for how `spans` is built.
    """

    __tablename__ = "chunk_positions"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: `[{page, page_width, page_height, rects: [[x0, y0, x1, y1], ...]}]`
    #: in PDF points, top-left origin.
    spans: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: "exact" | "fuzzy" | "none".
    match_quality: Mapped[str] = mapped_column(String(10), nullable=False)
