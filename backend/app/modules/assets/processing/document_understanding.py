"""Local AI document understanding: Qwen 3.5 4B through Ollama.

Deliberately separate from `extractors.py`: that module pulls raw text
out of file bytes deterministically (no model involved), and this
module is the one place an AI model is asked to understand text that
has *already* been extracted. Qwen is never asked to recover missing
text — an empty or unextractable document never reaches this module.

Reuses `app.core.llm.get_llm_gateway()` (Sprint 9A) rather than a
second, parallel Ollama HTTP client: LiteLLM's `ollama_chat/<model>`
notation already drives Ollama through the same gateway used for
Gemini, so this module only adds document-domain concerns — the
prompt, the output schema, the chunk-if-oversized/merge strategy, and
mapping failures onto `AIProfileStatus` — not a second transport layer.
"""

import json
from collections import Counter

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.config import settings
from app.core.llm import LLMGateway, get_llm_gateway
from app.core.logging.logger import get_logger
from app.modules.assets.processing.chunker import chunk_text

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a document analysis assistant. You are given text already "
    "extracted from a real document. Analyze only what is present in the "
    "text. Never invent facts, figures, or entities that are not in the "
    "text. Respond with ONLY a single JSON object matching the requested "
    "shape — no markdown code fences, no commentary before or after it."
)

# List caps keep the response well under `qwen_max_tokens`: uncapped, a
# benchmark-heavy paper (LLaMA, arXiv 2302.13971) made Qwen enumerate
# entities until the output was cut off mid-JSON on every attempt.
_USER_PROMPT_TEMPLATE = """Return a JSON object with exactly these fields:
{{
  "summary": string (2-4 sentences, grounded only in the text below),
  "keywords": array of at most 10 short strings,
  "entities": array of at most 15 plain strings naming the most \
important entities (people, organizations, places, figures) mentioned \
in the text -- each entity is a bare string like "ABC Poultry", never \
an object with "name"/"type" sub-fields,
  "topics": array of at most 5 short topic label strings,
  "language": ISO 639-1 language code of the text (e.g. "en")
}}

Text to analyze:
{text}"""


class DocumentUnderstandingError(Exception):
    """Raised for a domain-level failure: no text to analyze, or a
    response that cannot be turned into usable metadata.

    Distinct from `LLMError` (the model/provider itself is unreachable
    or misbehaving): this is raised when the model *responded* but the
    response was unusable, or when there was nothing to send it.
    """


#: Keys tried, in order, when a list item arrives as a labelled object
#: instead of a bare string (e.g. `{"name": "ABC Poultry"}`).
_LABEL_KEYS: tuple[str, ...] = ("name", "text", "value", "entity", "label", "title")


def _stringify_list_item(item: object) -> object:
    """Coerce one keywords/entities/topics list item to a label string.

    Returns the item unchanged if it isn't a recognizable near-miss
    shape, so Pydantic's own validation still rejects it — this never
    invents a label for something that doesn't have one.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in _LABEL_KEYS:
            label = item.get(key)
            if isinstance(label, str) and label.strip():
                return label
    return item


class QwenDocumentMetadata(BaseModel):
    """Structured output shape Qwen is asked to produce.

    Validated with Pydantic rather than parsed as loose text, per the
    "no fragile string parsing" requirement. Field validators coerce
    only the specific near-miss shapes a small local model plausibly
    produces (a single string instead of a one-item list); anything
    else fails validation rather than being guessed at.
    """

    summary: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    language: str = "unknown"

    @field_validator("keywords", "entities", "topics", mode="before")
    @classmethod
    def _coerce_near_miss_shapes(cls, value: object) -> object:
        """Tolerate the specific near-miss shapes observed from Qwen.

        Two coercions, both confirmed against live Qwen 3.5 output
        despite the prompt explicitly asking for plain strings:

        1. A bare string instead of an array (`"x"` instead of `["x"]`).
        2. A list item that is a labelled object instead of a bare
           string (`{"name": "ABC Poultry", "type": "Organization"}`
           instead of `"ABC Poultry"`) — observed on the real Sprint 9B
           acceptance-test document's entity list.

        Anything else (numbers, objects with no recognizable label
        key) is left as-is for Pydantic to reject as a genuine schema
        mismatch — this coerces known near-misses, it doesn't accept
        arbitrary shapes.
        """
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [_stringify_list_item(item) for item in value]
        return value


class DocumentUnderstandingResult(QwenDocumentMetadata):
    """Document metadata plus how much of the source was analyzed."""

    truncated: bool = False
    processed_sections: int = Field(default=1, gt=0)
    total_sections: int = Field(default=1, gt=0)


def _render_prompt(text: str) -> str:
    """Build the user prompt for one section of document text."""
    return _USER_PROMPT_TEMPLATE.format(text=text)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    """De-duplicate case-insensitively while keeping first-seen order and casing."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        stripped = item.strip()
        key = stripped.lower()
        if stripped and key not in seen:
            seen.add(key)
            result.append(stripped)
    return result


def _merge_sections(sections: list[QwenDocumentMetadata]) -> QwenDocumentMetadata:
    """Deterministically merge per-section metadata into one result.

    No second AI call: concatenating and de-duplicating is enough to
    produce a coherent combined result without the latency and
    additional-failure-surface of a merge-by-model pass, and keeps the
    strategy easy to verify by inspection.
    """
    summary = " ".join(section.summary.strip() for section in sections if section.summary.strip())
    languages = [section.language for section in sections if section.language]
    language = Counter(languages).most_common(1)[0][0] if languages else "unknown"
    return QwenDocumentMetadata(
        summary=summary or "No summary could be produced.",
        keywords=_dedupe_preserve_order([kw for s in sections for kw in s.keywords]),
        entities=_dedupe_preserve_order([e for s in sections for e in s.entities]),
        topics=_dedupe_preserve_order([t for s in sections for t in s.topics]),
        language=language,
    )


class QwenDocumentUnderstandingService:
    """Turns extracted document text into structured metadata via Qwen.

    Size strategy: text within `settings.qwen_max_input_characters` is
    sent in one call. Longer text is split with the existing
    `chunk_text()` utility (the same deterministic chunker the
    knowledge-base pipeline already uses, with no retrieval-oriented
    overlap here since these sections are independently summarized and
    merged, not retrieved), each section analyzed separately, and the
    results merged deterministically.
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or get_llm_gateway()

    async def analyze(self, text: str) -> DocumentUnderstandingResult:
        """Analyze extracted document text and return structured metadata.

        Raises `DocumentUnderstandingError` for empty text or an
        unusable model response, and lets `LLMError` subclasses
        (unreachable service, missing model, timeout) propagate
        unchanged so the caller can distinguish "Qwen answered badly"
        from "Qwen could not be reached".
        """
        stripped = text.strip()
        if not stripped:
            raise DocumentUnderstandingError(
                "Extracted text is empty; there is nothing for Qwen to analyze."
            )

        if len(stripped) <= settings.qwen_max_input_characters:
            metadata = await self._analyze_section(stripped)
            return DocumentUnderstandingResult.model_validate(metadata.model_dump())

        sections = chunk_text(
            stripped, chunk_size=settings.qwen_max_input_characters, chunk_overlap=0
        )
        total_sections = len(sections)
        if total_sections > settings.qwen_max_sections:
            sections = sections[: settings.qwen_max_sections]
            logger.warning(
                "document_understanding_truncated",
                character_count=len(stripped),
                budget=settings.qwen_max_input_characters,
                total_sections=total_sections,
                processed_sections=len(sections),
            )
        else:
            logger.info(
                "document_understanding_chunking",
                character_count=len(stripped),
                budget=settings.qwen_max_input_characters,
                section_count=len(sections),
            )
        # A section whose response is unusable is skipped, not fatal: a
        # paper's bibliography section can make Qwen list every cited
        # author until the output is cut off mid-JSON, and that one
        # section must not discard the rest of the document's metadata.
        analyzed: list[QwenDocumentMetadata] = []
        last_error: DocumentUnderstandingError | None = None
        for index, section in enumerate(sections):
            try:
                analyzed.append(await self._analyze_section(section))
            except DocumentUnderstandingError as exc:
                last_error = exc
                logger.warning(
                    "document_understanding_section_skipped", section_index=index, error=str(exc)
                )
        if not analyzed:
            raise last_error or DocumentUnderstandingError("No section could be analyzed.")
        merged = _merge_sections(analyzed)
        return DocumentUnderstandingResult(
            **merged.model_dump(),
            truncated=len(analyzed) < total_sections,
            processed_sections=len(analyzed),
            total_sections=total_sections,
        )

    async def _analyze_section(self, text: str) -> QwenDocumentMetadata:
        """Run one Qwen call and validate its output. `LLMError` propagates."""
        response = await self._gateway.generate(
            prompt=_render_prompt(text),
            system_prompt=_SYSTEM_PROMPT,
            model=f"ollama_chat/{settings.qwen_model}",
            think=settings.qwen_think,
            max_tokens=settings.qwen_max_tokens,
            timeout=settings.qwen_timeout,
            temperature=0.1,
            response_format={"type": "json_object"},
            num_ctx=settings.qwen_num_ctx,
        )
        return self._parse(response.content)

    @staticmethod
    def _parse(raw: str) -> QwenDocumentMetadata:
        """Validate the model's raw text as `QwenDocumentMetadata`.

        Both failure modes here are the model responding but the
        response being unusable — invalid JSON, or JSON that doesn't
        match the required shape — as opposed to the model being
        unreachable, which raises as an `LLMError` before this is ever
        called.
        """
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DocumentUnderstandingError(
                f"Qwen did not return valid JSON: {exc}"
            ) from exc

        try:
            return QwenDocumentMetadata.model_validate(payload)
        except ValidationError as exc:
            raise DocumentUnderstandingError(
                f"Qwen's JSON did not match the expected schema: {exc}"
            ) from exc


def get_document_understanding_service() -> QwenDocumentUnderstandingService:
    """Factory for `QwenDocumentUnderstandingService`, matching the
    `get_asset_processing_service`/`get_embedding_provider` pattern used
    elsewhere in this module."""
    return QwenDocumentUnderstandingService()
