"""Checks every number the narrative states against the result table.

The explain LLM sees only the result rows, so any number it writes that
is not (within rounding) one of those values is flagged to the user."""

import re
from typing import Any

_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?\s?(?:[kKmMbB](?![a-zA-Z])|%)?")
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}
_TOLERANCE = 0.01  # 1% relative: "1.23M" matches 1,234,567.89

# ponytail: integers 0-10 (by absolute value) are skipped as counting
# words ("top 3"); a narrative stating a genuinely wrong small number
# goes unflagged.
_SMALL_INT_MAX = 10


def _parse(token: str) -> float:
    cleaned = token.replace(",", "").replace(" ", "").rstrip("%")
    scale = _SCALE.get(cleaned[-1].lower(), 1.0) if cleaned[-1].isalpha() else 1.0
    return float(cleaned.rstrip("kKmMbB")) * scale


def _known_values(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        for value in row.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                values.append(float(value))
            else:
                values.extend(float(match) for match in re.findall(r"\d+(?:\.\d+)?", str(value)))
    return values


def _matches(number: float, known: list[float]) -> bool:
    return any(abs(number - value) <= max(abs(value) * _TOLERANCE, 0.005) for value in known)


def find_unverified_numbers(narrative: str, rows: list[dict[str, Any]]) -> list[str]:
    known = _known_values(rows)
    flagged: list[str] = []
    for match in _NUMBER.finditer(narrative):
        token = match.group(0).strip()
        number = _parse(token)
        # R2: skip by absolute value so small negative "counting words" (e.g. "-01"
        # extracted from a date-like label) are not falsely flagged either.
        if number.is_integer() and 0 <= abs(number) <= _SMALL_INT_MAX and not token.endswith("%"):
            continue
        # A "%" token may state either the raw number ("79" from a fraction
        # already expressed as a whole-number percent) or the fraction itself
        # (0.79) -- accept either. `_parse`'s suffix handling above (rstrip("%"))
        # is what makes `number` the whole-number reading here.
        matches = _matches(number, known) or (token.endswith("%") and _matches(number / 100, known))
        if not matches and token not in flagged:
            flagged.append(token)
    return flagged
