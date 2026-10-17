"""Independent chart value verification; model assertions are never verdicts."""
import json
import math
import re
from datetime import datetime
from decimal import Decimal, DecimalException
from fractions import Fraction
import sys
from typing import Any

from pydantic import ValidationError

from app.modules.research.visualization_contract import Visualization, VisualizationIntent, VisualizationOutcome

NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.])")
CONVERSIONS = {("g", "kg"): .001, ("kg", "g"): 1000, ("m", "km"): .001, ("km", "m"): 1000, ("fraction", "%"): 100, ("%", "fraction"): .01}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def order_trends(spec: dict, query: str) -> None:
    """Sort declared trends and remap verified provenance, preserving gaps.

    Null y values travel with their x; null/invalid x and duplicate x are
    rejected, never aggregated or bridged. Intentional trajectories retain order.
    """
    intent = spec["intent"]
    semantics = intent.get("axis_semantics")
    if re.search(r"\b(trajectory|trajectories|path)\b", query, re.I):
        semantics = "trajectory"
        intent["axis_semantics"] = semantics
    for ti, trace in enumerate(spec["data"]):
        if trace["type"] not in ("scatter", "scatter3d") or "lines" not in trace.get("mode", ""):
            continue
        axis = trace["x"]
        declared_trend = bool(re.search(r"\b(trend|chronological|chronologically)\b", query, re.I))
        effective = semantics
        if effective in (None, "category") and declared_trend:
            effective = "numeric" if all(isinstance(x, (int, float)) for x in axis) else "ordinal" if all(x in MONTHS for x in axis) else "date"
            intent["axis_semantics"] = effective
            intent["interpretation"] = f"Declared trend interpreted using {effective} x ordering."
        if effective is None or effective == "trajectory" or effective == "category":
            if effective is None:
                intent["interpretation"] = "Original path order retained; trend ordering was not declared."
            continue
        if any(x is None for x in axis) or len(set(axis)) != len(axis):
            raise ValueError("trend x coordinates must be present and unique; duplicates are not aggregated")
        if effective == "numeric":
            if not all(isinstance(x, (float, int)) and not isinstance(x, bool) for x in axis):
                raise ValueError("numeric trend requires numeric x coordinates")
            keys = axis
        elif effective == "date":
            try:
                dates = [datetime.fromisoformat(x.replace("Z", "+00:00")) for x in axis if isinstance(x, str)]
                if len(dates) != len(axis):
                    raise ValueError("date trend requires ISO date strings")
                aware = [d.tzinfo is not None for d in dates]
                if any(aware) and not all(aware):
                    raise ValueError("date trend cannot mix zoned and unzoned dates")
                keys = [d.isoformat() for d in dates] if not any(aware) else [d.timestamp() for d in dates]
                if len(set(keys)) != len(keys):
                    raise ValueError("date trend contains duplicate instants")
            except (ValueError, TypeError) as exc:
                raise ValueError("date trend requires unique valid ISO dates with consistent timezone semantics") from exc
        else:
            domain = intent.get("domain")
            if domain is None and all(x in MONTHS for x in axis):
                domain = list(MONTHS)
                intent["domain"] = domain
            if not domain or len(set(domain)) != len(domain) or any(x not in domain for x in axis):
                raise ValueError("ordinal trends require a unique domain containing every x category")
            keys = [domain.index(x) for x in axis]
            if set(keys) != set(range(min(keys), max(keys) + 1)):
                raise ValueError("ordinal trend has missing domain categories; supply explicit null observations to preserve gaps")
        order = sorted(range(len(axis)), key=lambda index: keys[index])
        inverse = {old: new for new, old in enumerate(order)}
        for field in (("x", "y", "z") if trace["type"] == "scatter3d" else ("x", "y")):
            trace[field] = [trace[field][index] for index in order]
        for ref in spec["provenance"]:
            if ref["trace"] == ti:
                ref["point"] = inverse[ref["point"]]


def requested_intent(query: str) -> VisualizationIntent | None:
    """Only explicit visual requests are inferred; never infer a line from years."""
    q = query.lower()
    if not re.search(r"\b(chart|plot|graph|diagram|flowchart|heatmap|visuali[sz](?:e|ation))\b", q):
        return None
    unsupported = re.search(r"\b(radar|candlestick|box|violin|histogram|sankey|treemap|funnel|polar|bubble)\s+(?:chart|plot|graph)\b", q)
    if unsupported:
        return VisualizationIntent(type=unsupported.group(1))
    explicit = re.search(r"\b(bar|line|scatter|pie|surface|contour)\s+(?:chart|plot|graph)\b", q)
    if explicit:
        return VisualizationIntent(type=explicit.group(1))
    for phrase, kind in (("bar", "bar"), ("pie", "pie"), ("heatmap", "heatmap"), ("contour", "contour"), ("surface", "surface"), ("scatter", "scatter"), ("line chart", "line"), ("trend", "line"), ("flowchart", "diagram"), ("diagram", "diagram")):
        if re.search(r"\b" + re.escape(phrase) + r"\b", q):
            return VisualizationIntent(type=kind)
    if re.search(r"\b(chart|plot|graph|visuali[sz](?:e|ation))\b", q):
        return VisualizationIntent(interpretation="Chart type was not specified; an explicit interpretation is required.")
    return None


def _numeric_values(spec: dict) -> dict[tuple, float]:
    values = {}
    for ti, trace in enumerate(spec["data"]):
        for field in ("x", "y", "z", "values"):
            for pi, value in enumerate(trace.get(field) or []):
                if isinstance(value, list):
                    for ci, cell in enumerate(value):
                        if isinstance(cell, (float, int)) and not isinstance(cell, bool):
                            values[(ti, field, pi, ci)] = cell
                elif isinstance(value, (float, int)) and not isinstance(value, bool):
                    values[(ti, field, pi, None)] = value
    return values


def verify_values(spec: dict, supplied: list[dict], full_text: dict[str, str]) -> None:
    """Each numeric coordinate resolves to a specific token in authorized evidence.

    Identity, arithmetic and a closed unit-conversion registry are deterministic.
    No eval, fuzzy matching, model-based verification, or self-attested tolerance.
    """
    by_id = {c["id"]: c for c in supplied if not c.get("simulated", True)}
    expected = _numeric_values(spec)
    seen = set()
    for ref in spec.get("provenance", []):
        key = (ref["trace"], ref["field"], ref["point"], ref.get("column"))
        if key not in expected or key in seen:
            raise ValueError("provenance must address each plotted numeric coordinate exactly once")
        seen.add(key)
        inputs = ref["inputs"]
        numbers = []
        units = []
        for source in inputs:
            citation = by_id.get(source["citation_id"])
            if citation is None or citation.get("reference") != source["reference"]:
                raise ValueError("value reference is outside the authorized evidence supplied for this run")
            for identity in ("chunk_id", "asset_id"):
                if source.get(identity) != citation.get(identity):
                    raise ValueError(f"value {identity} does not match authorized citation")
            chunk_id = citation.get("chunk_id")
            if chunk_id:
                text = full_text.get(chunk_id)
                if text is None:
                    raise ValueError("source chunk could not be resolved in the authorized project")
            else:
                text = citation.get("snippet", "")
            quote = source["quote"]
            if quote not in text:
                raise ValueError("value quote is absent from the actual source evidence")
            tokens = list(NUMBER.finditer(quote))
            index = source["token_index"]
            if index >= len(tokens):
                raise ValueError("plotted input differs from its indexed evidence number")
            try:
                raw_number = Decimal(tokens[index].group().replace(",", ""))
            except DecimalException as exc:
                raise ValueError("evidence numeric exponent exceeds supported precision") from exc
            if raw_number != 0 and source["value"] == 0:
                raise ValueError("evidence number underflows browser numeric precision")
            if raw_number != Decimal(str(source["value"])):
                raise ValueError("plotted input differs from its exact indexed evidence number")
            # Equality to a finite, bounded declared number must precede any
            # exponent-sensitive work. Canonicalize zero so even 0e-9999999999
            # never reaches Fraction's denominator/power construction.
            if raw_number.is_zero():
                raw_number = Decimal(0)
            if raw_number == raw_number.to_integral_value() and raw_number.copy_abs() > 9007199254740991:
                raise ValueError("evidence integer exceeds exact browser numeric precision")
            # Units are evidence, not model metadata. Dimensionless values use an
            # explicit empty unit; any claimed physical unit must occur in quote.
            if source["unit"] and not re.match(r"\s*" + re.escape(source["unit"]) + r"(?!\w)", quote[tokens[index].end():]):
                raise ValueError("input unit must directly qualify its indexed evidence number")
            trace = spec["data"][ref["trace"]]
            categories = []
            if ref["field"] in ("y", "values"):
                labels = trace.get("labels") if ref["field"] == "values" else trace.get("x")
                if labels and ref["point"] < len(labels):
                    categories = [labels[ref["point"]]]
            if ref["field"] == "z":
                if ref.get("column") is not None:
                    if not trace.get("x") or not trace.get("y"):
                        raise ValueError("grid value verification requires explicit source row/column coordinates")
                    categories = [trace["x"][ref["column"]], trace["y"][ref["point"]]]
                else:
                    categories = [trace["x"][ref["point"]], trace["y"][ref["point"]]]
            if categories:
                # Resolve one unambiguous observation, not arbitrary co-occurrence
                # anywhere in a passage containing several categories/numbers.
                position = tokens[index].start()
                cuts = list(re.finditer(r";|\n|(?<!\d)\.(?!\d)", quote))
                start = max((m.end() for m in cuts if m.end() <= position), default=0)
                end = min((m.start() for m in cuts if m.start() > position), default=len(quote))
                clause = quote[start:end]
                category_matches = [re.search(r"(?<!\w)" + re.escape(str(category)) + r"(?!\w)", clause) for category in categories]
                if any(match is None for match in category_matches):
                    raise ValueError("evidence observation does not associate this category/coordinate with its value")
                # A source record's numeric year/category is a coordinate, not
                # the observation. Finding it in the same clause must not let
                # a model reuse that label token as a y/z/derived input value.
                token_start, token_end = tokens[index].start() - start, tokens[index].end() - start
                if any(token_start < match.end() and token_end > match.start() for match in category_matches):
                    raise ValueError("observation input token overlaps its category/coordinate label; cite the separate measured-value token")
                expected_numbers = 1 + sum(len(list(NUMBER.finditer(str(category)))) for category in categories)
                if len(list(NUMBER.finditer(clause))) != expected_numbers:
                    raise ValueError("evidence observation contains ambiguous multiple values; supply an exact row or clause")
            numbers.append(Fraction(raw_number))
            units.append(source["unit"])
        formula, unit = ref["formula"], ref["unit"]
        if ref["field"] in ("x", "y", "z") and unit:
            layout = spec.get("layout", {})
            axes = layout.get("scene", {}) if spec["kind"] == "chart3d" else layout
            title = (axes.get(ref["field"] + "axis") or {}).get("title")
            title_text = title.get("text", "") if isinstance(title, dict) else title
            if title_text and not re.search(r"(?<!\w)" + re.escape(unit) + r"(?!\w)", title_text):
                raise ValueError("axis title must retain the independently verified output unit")
        if formula == "identity" and len(numbers) == 1 and units == [unit]:
            result = numbers[0]
        elif formula in ("sum", "difference") and all(u == unit for u in units) and (formula == "sum" or len(numbers) == 2):
            result = sum(numbers) if formula == "sum" else numbers[0] - numbers[1]
        elif formula == "ratio" and len(numbers) == 2 and units[0] == units[1] and unit == "fraction" and numbers[1] != 0:
            result = numbers[0] / numbers[1]
        elif formula == "convert" and len(numbers) == 1 and (units[0], unit) in CONVERSIONS:
            result = numbers[0] * Fraction(str(CONVERSIONS[(units[0], unit)]))
        else:
            raise ValueError("unsupported formula, input count, or incompatible units")
        if ref.get("rounding_decimals") is not None:
            result = round(result, ref["rounding_decimals"])
        plotted = Fraction(Decimal(str(expected[key])))
        computed_float = float(result)
        if not math.isfinite(computed_float) or result != 0 and computed_float == 0:
            raise ValueError("computed evidence value exceeds browser numeric precision")
        if result.denominator == 1 and abs(result) > 9007199254740991:
            raise ValueError("computed integer exceeds exact browser numeric precision")
        # Identity, zero, integer and explicitly rounded observations are exact.
        # Only nonintegral, unrounded derived arithmetic permits one binary64
        # ULP for ordinary floating evaluation (e.g. 0.1+0.2), never an absolute
        # floor or broad relative tolerance that could erase small observations.
        exact_required = formula == "identity" or result == 0 or result.denominator == 1 or abs(computed_float) < sys.float_info.min or ref.get("rounding_decimals") is not None
        matches = plotted == result if exact_required else abs(expected[key] - computed_float) <= math.ulp(computed_float)
        if not matches:
            raise ValueError("plotted value does not equal independently computed evidence value")
    if seen != set(expected):
        raise ValueError("every plotted numeric coordinate requires independently verifiable provenance")
    if not expected:
        raise ValueError("chart has no numeric observations")


def accept_visualization(*, raw: Any, query: str, supplied: list[dict], full_text: dict[str, str]) -> tuple[dict | None, dict | None]:
    intent = requested_intent(query)
    if raw is None and intent is None:
        return None, None
    requested = intent or VisualizationIntent(interpretation="Visualization supplied without an explicit visual request.")
    def outcome(status, reason, verification="unverified", repairs=0):
        return VisualizationOutcome(status=status, reason=reason, requested=requested, verification=verification, repair_attempts=repairs, next_action=None if status == "produced" else "Supply source data with point-level references, or choose a supported chart type.").model_dump(mode="json")
    if requested.type is not None and requested.type not in ("bar", "line", "scatter", "pie", "heatmap", "contour", "surface", "scatter3d", "diagram"):
        return None, outcome("unsupported", f"Requested {requested.type} visualization is not supported by the renderer.")
    if raw is None:
        return None, outcome("not_enough_data", "The answer is available, but the requested visualization was not supplied with sufficient data.")
    repairs = 0
    if isinstance(raw, dict) and isinstance(raw.get("data"), list) and raw["data"] and isinstance(raw["data"][0], dict) and "type" not in raw["data"][0] and requested.type:
        raw = {**raw, "intent": requested.model_dump(mode="json")}
        repairs = 1  # One deterministic normalization; no extra LLM/stage calls.
    try:
        spec = Visualization.model_validate(raw).model_dump(mode="json", exclude_none=True)
        if requested.type is None:
            requested.interpretation = "Interpreted the ambiguous visual request as " + ("diagram" if spec["kind"] == "diagram" else ", ".join(dict.fromkeys(t["type"] for t in spec["data"]))) + "."
        if requested.type:
            actual = {t["type"] for t in spec["data"]}
            required = "scatter" if requested.type == "line" else requested.type
            if requested.type == "diagram":
                if spec["kind"] != "diagram":
                    raise ValueError("requested diagram was not produced")
            elif actual != {required} or (requested.type == "line" and any("lines" not in t.get("mode", "") for t in spec["data"])):
                raise ValueError(f"requested {requested.type} intent does not match produced trace types")
        if spec["kind"] != "diagram":
            verify_values(spec, supplied, full_text)
            order_trends(spec, query)
            requested.axis_semantics = spec["intent"].get("axis_semantics")
            requested.domain = spec["intent"].get("domain")
            requested.metrics = spec["intent"].get("metrics", [])
            requested.series = spec["intent"].get("series", [])
            if spec["intent"].get("interpretation"):
                requested.interpretation = spec["intent"]["interpretation"]
            # Actual source context overrides an attempted measured classification.
            by_id = {c["id"]: c for c in supplied}
            for point in spec["provenance"]:
                for source in point["inputs"]:
                    citation = by_id[source["citation_id"]]
                    text = full_text.get(citation.get("chunk_id"), citation.get("snippet", ""))
                    if re.search(r"\b(illustrative|hypothetical|synthetic|simulated|example data|sample values)\b", text, re.I):
                        spec["origin"] = "illustrative"
            if spec["origin"] in ("unknown", "user_entered"):
                raise ValueError("measured/derived/illustrative origin must be declared; user values require a trusted input path")
            derived = any(p["formula"] != "identity" or p.get("rounding_decimals") is not None for p in spec["provenance"])
            if derived and spec["origin"] == "measured":
                raise ValueError("transformed values must be labelled derived or illustrative")
            if any(t["type"] == "pie" for t in spec["data"]):
                spec["scope"] = {"label": "Share of displayed categories", "partial": True, "description": "Full distribution coverage is unverified."}
        return spec, outcome("produced", "Visualization values were checked independently against supplied evidence." if spec["kind"] != "diagram" else "Diagram supplied; numeric data verification does not apply.", "illustrative" if spec["origin"] == "illustrative" else "verified" if spec["kind"] != "diagram" else "unverified", repairs)
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError) as exc:
        return None, outcome("failed", "Visualization withheld: " + str(exc)[:1500], repairs=repairs)
