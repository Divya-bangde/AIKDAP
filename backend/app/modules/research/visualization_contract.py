"""Bounded, renderer-aligned chart contract. No arbitrary Plotly configuration.

Limits are deployment configurable (VISUALIZATION_MAX_* environment variables).
They also apply to legacy reads; malformed stored visuals never break an answer.
"""
import json
import math
import os
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator

MAX_TRACES = int(os.getenv("VISUALIZATION_MAX_TRACES", "32"))
MAX_POINTS = int(os.getenv("VISUALIZATION_MAX_POINTS", "10000"))
MAX_TOTAL = int(os.getenv("VISUALIZATION_MAX_TOTAL_POINTS", "50000"))
MAX_GRID = int(os.getenv("VISUALIZATION_MAX_GRID_CELLS", "40000"))
MAX_LABEL = int(os.getenv("VISUALIZATION_MAX_LABEL_LENGTH", "500"))
MAX_BYTES = int(os.getenv("VISUALIZATION_MAX_BYTES", "2000000"))
MAX_DIAGRAM = int(os.getenv("VISUALIZATION_MAX_DIAGRAM_CHARS", "20000"))
SUPPORTED_2D = ("bar", "scatter", "pie", "heatmap", "contour")
SUPPORTED_3D = ("surface", "scatter3d")
Scalar = StrictFloat | StrictInt | StrictStr | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class VisualizationKind(str, Enum):
    CHART2D = "chart2d"
    CHART3D = "chart3d"
    DIAGRAM = "diagram"


class VisualizationIntent(StrictModel):
    type: str | None = Field(default=None, max_length=MAX_LABEL)
    axis_semantics: Literal["category", "numeric", "date", "ordinal", "trajectory"] | None = None
    metrics: list[str] = Field(default_factory=list, max_length=MAX_TRACES)
    series: list[str] = Field(default_factory=list, max_length=MAX_TRACES)
    interpretation: str | None = Field(default=None, max_length=MAX_LABEL)
    domain: list[StrictStr | StrictFloat | StrictInt] | None = Field(default=None, max_length=MAX_POINTS)


class VisualizationOutcome(StrictModel):
    status: Literal["produced", "not_enough_data", "unsupported", "failed"]
    reason: str
    next_action: str | None = None
    requested: VisualizationIntent = Field(default_factory=VisualizationIntent)
    verification: Literal["verified", "illustrative", "user_entered", "unverified"] = "unverified"
    repair_attempts: int = Field(default=0, ge=0, le=1)


class TextTitle(StrictModel):
    text: str = Field(max_length=MAX_LABEL)


class Axis(StrictModel):
    title: str | TextTitle | None = None
    type: Literal["linear", "log", "date", "category"] | None = None
    range: list[float] | None = Field(default=None, min_length=2, max_length=2)
    categoryorder: Literal["array", "trace"] | None = None
    categoryarray: list[Scalar] | None = Field(default=None, max_length=MAX_POINTS)


class Layout(StrictModel):
    title: str | TextTitle | None = None
    xaxis: Axis | None = None
    yaxis: Axis | None = None
    scene: dict[Literal["xaxis", "yaxis", "zaxis"], Axis] | None = None
    barmode: Literal["group", "stack", "relative", "overlay"] | None = None
    showlegend: bool | None = None
    height: int | None = Field(default=None, ge=100, le=2000)


class Trace(StrictModel):
    name: str = Field(default="", max_length=MAX_LABEL)
    uid: str | None = Field(default=None, min_length=1, max_length=MAX_LABEL, pattern=r"^[A-Za-z0-9_-]+$")


class XYTrace(Trace):
    type: Literal["bar", "scatter", "scatter3d"]
    x: list[Scalar] = Field(min_length=1, max_length=MAX_POINTS)
    y: list[Scalar] = Field(min_length=1, max_length=MAX_POINTS)
    z: list[Scalar] | None = Field(default=None, max_length=MAX_POINTS)
    mode: Literal["lines", "markers", "lines+markers"] | None = None
    connectgaps: Literal[False] = False

    @model_validator(mode="after")
    def paired(self) -> Self:
        if len(self.x) != len(self.y):
            raise ValueError("x and y arrays must have equal lengths")
        if self.type == "scatter3d":
            if self.z is None or len(self.z) != len(self.x):
                raise ValueError("scatter3d requires paired x/y/z arrays")
        elif self.z is not None:
            raise ValueError("2D traces cannot carry z")
        return self


class PieTrace(Trace):
    type: Literal["pie"]
    labels: list[StrictStr] = Field(min_length=1, max_length=min(MAX_POINTS, 50))
    values: list[StrictFloat | StrictInt] = Field(min_length=1, max_length=min(MAX_POINTS, 50))

    @model_validator(mode="after")
    def positive_distribution(self) -> Self:
        if len(self.labels) != len(self.values) or len(set(self.labels)) != len(self.labels):
            raise ValueError("pie labels/values must be paired with unique categories")
        if any(v < 0 for v in self.values) or sum(self.values) <= 0:
            raise ValueError("pie values must be nonnegative with a positive total")
        return self


class GridTrace(Trace):
    type: Literal["heatmap", "contour", "surface"]
    z: list[list[StrictFloat | StrictInt | None]] = Field(min_length=1, max_length=MAX_POINTS)
    x: list[Scalar] | None = Field(default=None, max_length=MAX_POINTS)
    y: list[Scalar] | None = Field(default=None, max_length=MAX_POINTS)

    @model_validator(mode="after")
    def rectangular(self) -> Self:
        width = len(self.z[0])
        if not width or any(len(row) != width for row in self.z):
            raise ValueError("z grid must be nonempty and rectangular")
        if width * len(self.z) > MAX_GRID:
            raise ValueError(f"grid exceeds {MAX_GRID} cells")
        if self.x is not None and len(self.x) != width or self.y is not None and len(self.y) != len(self.z):
            raise ValueError("grid x/y coordinates must match z columns/rows")
        return self


ChartTrace = Annotated[XYTrace | PieTrace | GridTrace, Field(discriminator="type")]


class EvidenceInput(StrictModel):
    citation_id: str
    reference: str
    chunk_id: str | None = None
    asset_id: str | None = None
    quote: str = Field(min_length=1, max_length=5000)
    token_index: int = Field(ge=0)
    value: StrictFloat | StrictInt
    unit: str = Field(max_length=MAX_LABEL)

    @model_validator(mode="after")
    def safe_numeric_input(self) -> Self:
        if isinstance(self.value, int) and abs(self.value) > 9007199254740991:
            raise ValueError("integer input exceeds exact browser numeric precision")
        return self


class ValueProvenance(StrictModel):
    trace: int = Field(ge=0, lt=MAX_TRACES)
    field: Literal["x", "y", "z", "values"]
    point: int = Field(ge=0)
    column: int | None = Field(default=None, ge=0)
    inputs: list[EvidenceInput] = Field(min_length=1, max_length=16)
    formula: Literal["identity", "sum", "difference", "ratio", "convert"] = "identity"
    unit: str = Field(max_length=MAX_LABEL)
    rounding_decimals: int | None = Field(default=None, ge=0, le=12)


class Scope(StrictModel):
    label: str | None = Field(default=None, max_length=MAX_LABEL)
    partial: bool = False
    description: str | None = Field(default=None, max_length=MAX_LABEL)


def _validate_tree(value: Any) -> None:
    if isinstance(value, str) and len(value) > MAX_LABEL:
        raise ValueError(f"label exceeds {MAX_LABEL} characters")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("all chart values must be finite")
    if isinstance(value, int) and abs(value) > 9007199254740991:
        raise ValueError("integer coordinate exceeds exact browser numeric precision")
    if isinstance(value, dict):
        for child in value.values():
            _validate_tree(child)
    if isinstance(value, list):
        for child in value:
            _validate_tree(child)


class Visualization(StrictModel):
    kind: VisualizationKind
    title: str = Field(default="", max_length=MAX_LABEL)
    data: list[ChartTrace] = Field(default_factory=list, max_length=MAX_TRACES)
    layout: Layout = Field(default_factory=Layout)
    mermaid: str | None = Field(default=None, max_length=MAX_DIAGRAM)
    origin: Literal["measured", "derived", "illustrative", "user_entered", "unknown"] = "unknown"
    provenance: list[ValueProvenance] = Field(default_factory=list, max_length=MAX_TOTAL)
    intent: VisualizationIntent = Field(default_factory=VisualizationIntent)
    scope: Scope | None = None

    @model_validator(mode="before")
    @classmethod
    def budgets_and_points(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        # Bounded encoding before Pydantic allocates individual trace/provenance models.
        encoder = json.JSONEncoder(allow_nan=False, ensure_ascii=False)
        size = 0
        try:
            for part in encoder.iterencode(value):
                size += len(part.encode("utf-8"))
                if size > MAX_BYTES:
                    raise ValueError(f"visualization exceeds {MAX_BYTES} serialized bytes")
        except (TypeError, OverflowError, RecursionError) as exc:
            raise ValueError("visualization must be finite JSON") from exc
        points = value.get("data")
        point_axes = {"x", "y", "z"} if value.get("kind") == "chart3d" else {"x", "y"}
        if isinstance(points, list) and points and isinstance(points[0], dict) and set(points[0]) == point_axes:
            if not all(isinstance(p, dict) and set(p) == point_axes for p in points):
                raise ValueError("all point objects must contain the same coordinates")
            raw_intent = value.get("intent") or {}
            if not isinstance(raw_intent, dict):
                raise ValueError("visualization intent must be an object")
            intent_type = raw_intent.get("type")
            if intent_type not in ("bar", "line", "scatter", "scatter3d"):
                raise ValueError("point lists require explicit bar/line/scatter/scatter3d intent")
            trace = {"type": "bar" if intent_type == "bar" else "scatter3d" if intent_type == "scatter3d" else "scatter", **{axis: [p[axis] for p in points] for axis in point_axes}}
            if intent_type == "line":
                trace["mode"] = "lines+markers"
            if intent_type == "scatter3d":
                trace["mode"] = "markers"
            value = {**value, "data": [trace]}
        _validate_tree(value.get("data", []))
        _validate_tree(value.get("layout", {}))
        return value

    @model_validator(mode="after")
    def content(self) -> Self:
        if self.kind == VisualizationKind.DIAGRAM:
            if not self.mermaid or not self.mermaid.strip() or self.data:
                raise ValueError("diagram requires Mermaid source and no chart traces")
            return self
        if not self.data:
            raise ValueError("chart requires at least one supported trace")
        identities = [trace.uid for trace in self.data if trace.uid is not None]
        if len(identities) != len(set(identities)):
            raise ValueError("provided trace uids must be unique within a visualization")
        total = 0
        for trace in self.data:
            if (trace.type in SUPPORTED_3D) != (self.kind == VisualizationKind.CHART3D):
                raise ValueError("trace dimension must match chart2d/chart3d")
            total += sum(len(row) for row in trace.z) if isinstance(trace, GridTrace) else len(trace.values) if isinstance(trace, PieTrace) else len(trace.x)
            if isinstance(trace, XYTrace):
                for axis in (trace.x, trace.y, trace.z):
                    if axis is None:
                        continue
                    kinds = {"number" if isinstance(v, (int, float)) else "text" for v in axis if v is not None}
                    if len(kinds) > 1:
                        raise ValueError("axis cannot mix numeric and category/date values")
                    if trace.type == "scatter3d" and "text" in kinds:
                        raise ValueError("scatter3d coordinates must be numeric")
        if total > MAX_TOTAL:
            raise ValueError(f"chart exceeds {MAX_TOTAL} total points/cells")
        axes = self.layout.scene if self.kind == VisualizationKind.CHART3D else {name: getattr(self.layout, name) for name in ("xaxis", "yaxis")}
        for axis_name, axis_layout in (axes or {}).items():
            if axis_layout is None:
                continue
            for trace in self.data:
                for value in getattr(trace, axis_name[0], None) or []:
                    cells = value if isinstance(value, list) else [value]
                    for cell in cells:
                        if cell is None:
                            continue
                        if axis_layout.type in ("linear", "log"):
                            if not isinstance(cell, (int, float)) or isinstance(cell, bool):
                                raise ValueError("linear/log axes require numeric coordinates")
                            if axis_layout.type == "log" and cell <= 0:
                                raise ValueError("log axes require strictly positive coordinates")
                        if axis_layout.type == "date":
                            try:
                                datetime.fromisoformat(str(cell).replace("Z", "+00:00"))
                            except ValueError as exc:
                                raise ValueError("date axis requires valid ISO dates") from exc
                        if axis_layout.range is not None:
                            if not isinstance(cell, (int, float)) or axis_layout.type in ("date", "category"):
                                raise ValueError("explicit axis ranges require numeric coordinates")
                            coordinate = math.log10(cell) if axis_layout.type == "log" else cell
                            if not axis_layout.range[0] <= coordinate <= axis_layout.range[1]:
                                raise ValueError("explicit axis range must include every plotted coordinate")
        return self
