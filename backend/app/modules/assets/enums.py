"""Enumerations for the assets module."""

import enum


class AssetType(str, enum.Enum):
    """The kind of content an asset represents."""

    DOCUMENT = "document"
    DATASET = "dataset"
    REPORT = "report"
    SUMMARY = "summary"
    CHART = "chart"
    PRESENTATION = "presentation"
    IMAGE = "image"
    NOTE = "note"
    WORKFLOW_OUTPUT = "workflow_output"
    OTHER = "other"


class AssetStatus(str, enum.Enum):
    """Lifecycle state of an asset."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class AssetSource(str, enum.Enum):
    """How an asset came to exist in the platform."""

    UPLOAD = "upload"
    GENERATED = "generated"
    IMPORTED = "imported"


class EmbeddingStatus(str, enum.Enum):
    """Status of the (future) embedding-generation pipeline for an asset
    or knowledge chunk. Reused by `app.modules.knowledge_base` rather
    than duplicated, since it's the same underlying concept."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class AssetProcessingStatus(str, enum.Enum):
    """State of the extract -> chunk -> (future) embed pipeline for an
    asset. Distinct from `AssetStatus`, which tracks lifecycle
    (active/archived), not pipeline progress.

    `UNSUPPORTED` is a deliberate non-error terminal state: it means no
    extractor is implemented yet for the asset's MIME type (a known,
    expected gap), as opposed to `FAILED`, which means something broke
    while processing a MIME type we do support.

    `OCR_REQUIRED` (Sprint 12.1) is a third, equally deliberate terminal
    state: the file parsed structurally (it IS a valid PDF) but
    contained zero machine-readable text — the signature of a scanned
    document. Distinct from both `UNSUPPORTED` (we don't have any
    extractor for this MIME type at all) and `FAILED` (the file didn't
    parse) — this format IS supported, this specific file just needs
    OCR, which isn't available yet.
    """

    PENDING = "pending"
    QUEUED = "queued"
    #: Milestone 10 step 4: a report-generation Celery job is actively
    #: running the LangGraph pipeline for a GENERATED asset. Not part of
    #: the extract/chunk/embed pipeline's own vocabulary (EXTRACTING/
    #: CHUNKING/EMBEDDING) -- reports skip straight from PENDING to
    #: RUNNING to COMPLETED/FAILED.
    RUNNING = "running"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    OCR_REQUIRED = "ocr_required"
