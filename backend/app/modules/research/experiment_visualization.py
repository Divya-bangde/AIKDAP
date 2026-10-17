"""Read-only preflight for experiment charts before trace arrays are allocated."""
import json
import math
from decimal import Decimal

from app.modules.research import visualization_contract as budgets


def source_reference_bytes(reference) -> int:
    if reference is None:
        return 4  # Explicit unknown, never fabricated source identity.
    if len(reference.paper_title) > budgets.MAX_LABEL or len(reference.quote or "") > 5000:
        raise ValueError("test-case source label/quote exceeds visualization context limits; inspect the original test-case source")
    return len(json.dumps(reference.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))


def finite_x(value: str) -> None:
    try:
        numeric = float(value)
    except ValueError:
        return  # Categorical input is intentional, not a failed number.
    if not math.isfinite(numeric):
        raise ValueError("numeric x coordinates must be finite; correct NaN/Infinity in the saved test cases")
    exact = Decimal(value)
    if exact == exact.to_integral_value() and abs(exact) > 9007199254740991:
        raise ValueError("numeric coordinate exceeds exact browser integer precision; use the test-case table/source")
    if numeric == 0 and exact != 0:
        raise ValueError("numeric coordinate underflows browser precision; use the test-case table/source")


def preflight_test_cases(cases, input_name: str, output_name: str, group_by: str | None) -> None:
    """No trimming: reject an oversized chart and preserve all saved test cases.

    Byte accounting conservatively reserves bounded labels, trend-note and JSON
    framing before allocating arrays. Final response schema checks actual bytes.
    """
    for label in (input_name, output_name, group_by):
        if label is not None and len(label) > budgets.MAX_LABEL:
            raise ValueError(f"chart labels exceed {budgets.MAX_LABEL} characters")
    if group_by is None and len(f"{output_name} vs {input_name}") > budgets.MAX_LABEL:
        raise ValueError(f"series labels exceed {budgets.MAX_LABEL} characters")
    counts = {}
    total = 0
    byte_size = 256 + 24 * budgets.MAX_LABEL  # UTF-8 label/note reserve, up to 4 bytes/character.
    for case in cases:
        if input_name not in case.inputs:
            continue
        x = case.inputs[input_name]
        if len(case.id) > budgets.MAX_LABEL:
            raise ValueError(f"test-case identities exceed {budgets.MAX_LABEL} characters")
        if len(x) > budgets.MAX_LABEL:
            raise ValueError(f"x labels exceed {budgets.MAX_LABEL} characters")
        finite_x(x)
        group = case.inputs.get(group_by, "(not set)") if group_by else ""
        name = f"{group_by} = {group}" if group_by else f"{output_name} vs {input_name}"
        if len(name) > budgets.MAX_LABEL:
            raise ValueError(f"series labels exceed {budgets.MAX_LABEL} characters")
        if group not in counts:
            if len(counts) >= budgets.MAX_TRACES:
                raise ValueError(f"chart exceeds {budgets.MAX_TRACES} series; choose a different comparison input")
            counts[group] = 0
            byte_size += len(json.dumps(name, ensure_ascii=False).encode("utf-8")) + 80
        counts[group] += 1
        total += 1
        if counts[group] > budgets.MAX_POINTS or total > budgets.MAX_TOTAL:
            raise ValueError(f"chart exceeds {budgets.MAX_POINTS} points per series or {budgets.MAX_TOTAL} total points; select a smaller test-case scope")
        y = None
        for output in case.expected_outputs:
            if output.output_name == output_name:
                try:
                    y = float(output.value)
                except ValueError:
                    pass  # Existing nonnumeric output policy: explicit null gap.
                if y is not None and not math.isfinite(y):
                    raise ValueError("numeric y coordinates must be finite; correct NaN/Infinity in the saved test cases")
                if y is not None:
                    finite_x(output.value)  # Also checks original numeric precision before float conversion.
                break
        byte_size += len(json.dumps(x, ensure_ascii=False).encode("utf-8")) + len(json.dumps(y)) + len(json.dumps(case.id, ensure_ascii=False).encode("utf-8")) + source_reference_bytes(case.source_reference) + 12
        if byte_size > budgets.MAX_BYTES:
            raise ValueError(f"chart exceeds {budgets.MAX_BYTES} serialized bytes (including reserved context); select a smaller test-case scope")
