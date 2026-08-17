"""Shared result types for every engine."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Step:
    """One observable event in a resolution, for debugging and transparency."""

    kind: str            # navigate | click | wait | api | redirect | error
    detail: str
    url: str = ""
    at: float = field(default_factory=lambda: round(time.time(), 3))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Result:
    """Outcome of resolving one link."""

    input: str
    url: str | None = None          # the destination, when found
    ok: bool = False
    engine: str = ""
    confidence: float = 0.0         # 0..1, how sure we are this is the target
    error: str | None = None
    candidates: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    elapsed: float = 0.0

    def log(self, kind: str, detail: str, url: str = "") -> None:
        self.steps.append(Step(kind, detail, url))

    def succeed(self, url: str, engine: str, confidence: float) -> "Result":
        self.url = url
        self.ok = True
        self.engine = engine
        self.confidence = confidence
        return self

    def fail(self, error: str) -> "Result":
        self.ok = False
        self.error = error
        return self

    def to_dict(self, verbose: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "input": self.input,
            "url": self.url,
            "engine": self.engine,
            "confidence": round(self.confidence, 3),
            "elapsed": round(self.elapsed, 2),
            "error": self.error,
        }
        if verbose:
            d["candidates"] = self.candidates
            d["steps"] = [s.to_dict() for s in self.steps]
        return d
