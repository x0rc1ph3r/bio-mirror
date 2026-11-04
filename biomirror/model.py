"""Data models shared by BIO-Mirror's parser and verification engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RawEvent:
    """A monitor event exactly as observed in a simulator log."""

    line_no: int
    cycle: int
    signal: str
    index: int
    old: str
    new: str
    raw_line: str

    @property
    def stream(self) -> str:
        return f"{self.signal}[{self.index}]"

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_no": self.line_no,
            "cycle": self.cycle,
            "signal": self.signal,
            "index": self.index,
            "stream": self.stream,
            "old": self.old,
            "new": self.new,
        }


@dataclass(frozen=True)
class CanonicalEvent:
    """An event normalized to a stable origin and per-stream interval."""

    sequence: int
    line_no: int
    signal: str
    index: int
    old: str
    new: str
    raw_cycle: int
    relative_cycle: int
    delta_cycles: int | None

    @property
    def stream(self) -> str:
        return f"{self.signal}[{self.index}]"

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "line_no": self.line_no,
            "stream": self.stream,
            "signal": self.signal,
            "index": self.index,
            "old": self.old,
            "new": self.new,
            "raw_cycle": self.raw_cycle,
            "relative_cycle": self.relative_cycle,
            "delta_cycles": self.delta_cycles,
        }
