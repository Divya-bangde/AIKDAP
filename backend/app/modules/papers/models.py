"""PaperReference ORM model: one document asset's OpenAlex identity."""

import enum
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import BaseModel


class OpenAlexStatus(str, enum.Enum):
    """Whether a paper has been matched to an OpenAlex work."""

    #: Not looked up yet (or the lookup could not reach OpenAlex).
    PENDING = "pending"
    MATCHED = "matched"
    #: Looked up and no confident match exists. Never a guess.
    NOT_FOUND = "not_found"


class PaperReference(BaseModel):
    """The OpenAlex match for one PDF document asset.

    A separate table rather than columns on `assets`: only paper-like
    documents have one, and every field is bibliographic, not about the
    file. Stored as a plain string status (not a native enum) so adding
    a state never needs a type migration.
    """

    __tablename__ = "paper_references"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Short OpenAlex work id, e.g. "W2741809807".
    openalex_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Bare DOI, lowercase, without the "https://doi.org/" prefix.
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cited_by_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Short OpenAlex ids of the works this paper cites.
    referenced_works: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    openalex_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OpenAlexStatus.PENDING.value, server_default="pending"
    )

    def apply_work(self, work: dict[str, Any]) -> None:
        """Copy a parsed OpenAlex work (`openalex.parse_work`) onto the row."""
        self.openalex_id = work["openalex_id"]
        self.doi = work["doi"] or self.doi
        self.title = work["title"]
        self.publication_year = work["publication_year"]
        self.cited_by_count = work["cited_by_count"]
        self.referenced_works = work["referenced_works"]
        self.openalex_status = OpenAlexStatus.MATCHED.value
