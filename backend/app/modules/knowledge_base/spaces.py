"""The complete, immutable identity of a document/query embedding space."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EmbeddingSpace:
    provider: str
    model: str
    version: str
    dimensions: int
    document_task: str | None = None
    query_task: str | None = None

    @property
    def id(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    def as_dict(self) -> dict:
        return {"id": self.id, **asdict(self)}


def validate_vectors(vectors, count: int, dimensions: int) -> None:
    if len(vectors) != count:
        raise ValueError("Embedding response count differs from source count.")
    for vector in vectors:
        if len(vector) != dimensions or not all(math.isfinite(float(v)) for v in vector):
            raise ValueError("Embedding dimension or finite-number validation failed.")
        if not any(float(v) != 0 for v in vector):
            raise ValueError("Zero vectors cannot be searched by cosine distance.")
