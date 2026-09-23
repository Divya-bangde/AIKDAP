"""Where an answer's support came from: project documents, the web, or
general knowledge (visualization Phase 1d).

Answers are not segmented into cited sentences, so the unit is
citations:

- a research answer counts each citation whose `[cN]` marker appears in
  the answer text (claims and claim-only evidence stored alongside the
  citations are not counted; simulated placeholders never are), plus 1
  `general` when the answer was given from general knowledge
  (`grounding_status == "unsourced"`);
- a report asset counts its sections' document citations as `kb` and
  each distinct link in its sections as `web`.

Pure functions over already-stored data, so saving and backfilling use
the same code.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")

UNSOURCED = "unsourced"


class SourceMix(BaseModel):
    """API shape of a stored source mix: counts per origin, and what was counted."""

    kb: int
    web: int
    general: int
    unit: Literal["citations"]


def _mix(kb: int, web: int, general: int) -> dict[str, Any]:
    return {"kb": kb, "web": web, "general": general, "unit": "citations"}


def compute_run_source_mix(
    answer: str | None, citations: list[dict[str, Any]] | None, grounding_status: str | None
) -> dict[str, Any]:
    """Source mix for a research run's answer."""
    answer = answer or ""
    real = [
        item
        for item in citations or []
        if item.get("kind") != "claim" and not item.get("simulated", False) and item.get("id")
    ]
    cited = [item for item in real if f"[{item['id']}]" in answer]
    # An answer that carries no inline markers at all (e.g. the
    # extractive synthesizer) cites everything it stored.
    counted = cited if cited or "[c" in answer else real
    kb = sum(1 for item in counted if item.get("source") == "asset")
    web = sum(1 for item in counted if item.get("source") == "web")
    general = 1 if grounding_status == UNSOURCED else 0
    return _mix(kb, web, general)


def compute_report_source_mix(sections: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Source mix for a generated report asset (synopsis / build plan)."""
    kb = 0
    urls: set[str] = set()
    for section in sections or []:
        kb += len(section.get("citations") or [])
        # Sentence punctuation after a link is not part of it.
        urls.update(url.rstrip(".,;:!?") for url in _URL_RE.findall(section.get("content") or ""))
    return _mix(kb, len(urls), 0)
