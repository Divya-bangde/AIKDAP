"""Pydantic v2 schemas for the project paper map (Phase 4)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class PaperNode(BaseModel):
    """One paper in the graph, keyed by its short OpenAlex id."""

    id: str
    title: str
    year: int | None
    cited_by_count: int | None
    #: `uploaded` by the user, imported from an OpenAlex `suggested`
    #: paper (Add & Re-run), or an `external` work cited by the project.
    kind: Literal["uploaded", "suggested", "external"]
    in_project: bool
    doi: str | None = None
    #: The project asset, for in-project papers.
    asset_id: uuid.UUID | None = None


class PaperLink(BaseModel):
    """`source` cites `target` (both short OpenAlex ids)."""

    source: str
    target: str


class UnmatchedPaper(BaseModel):
    """A project PDF with no OpenAlex match (yet), so it has no edges."""

    paper_id: uuid.UUID
    title: str
    #: `not_found` (looked up, no confident match) or `pending`.
    status: str


class PaperGraph(BaseModel):
    nodes: list[PaperNode]
    links: list[PaperLink]
    unmatched: list[UnmatchedPaper]
    #: True when outside works were requested but OpenAlex could not be
    #: reached, so the graph shows project papers only.
    external_unavailable: bool = False


class ProjectPaperImportRequest(BaseModel):
    """Add one OpenAlex work (an `external` graph node) to the project."""

    openalex_id: str = Field(pattern=r"^W\d+$")


class ProjectPaperImportAccepted(BaseModel):
    openalex_id: str
    status: Literal["queued"]
