"""Parsing for Verilator monitor output emitted by ``bio-sim``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .model import RawEvent


class ParseError(ValueError):
    """Raised when a monitor log cannot be interpreted safely."""


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_EVENT_RE = re.compile(
    r"\[mon\]\s+cyc=(?P<cycle>\d+)\b"
    r"(?:\s+\([^\r\n)]*\))?\s+"
    r"(?P<signal>[A-Za-z_][\w.]*)\[(?P<index>\d+)\]\s*:\s*"
    r"(?P<old>[01xXzZ]+)\s*->\s*(?P<new>[01xXzZ]+)"
)


def strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def parse_lines(lines: Iterable[str], *, strict: bool = False) -> list[RawEvent]:
    """Parse bit-transition monitor records while ignoring unrelated output.

    Equal-cycle events are legal and retain log order. A backwards cycle is
    rejected because relative timing would otherwise be ambiguous.
    """

    events: list[RawEvent] = []
    last_cycle: int | None = None
    for line_no, raw_line in enumerate(lines, start=1):
        clean = strip_ansi(raw_line.rstrip("\r\n"))
        match = _EVENT_RE.search(clean)
        if match is None:
            if strict and "[mon]" in clean and "cyc=" in clean and "->" in clean:
                raise ParseError(f"malformed monitor transition at line {line_no}: {clean}")
            continue

        cycle = int(match.group("cycle"))
        if last_cycle is not None and cycle < last_cycle:
            raise ParseError(
                f"monitor cycle moved backwards at line {line_no}: "
                f"{cycle} after {last_cycle}"
            )
        last_cycle = cycle
        events.append(
            RawEvent(
                line_no=line_no,
                cycle=cycle,
                signal=match.group("signal"),
                index=int(match.group("index")),
                old=match.group("old").lower(),
                new=match.group("new").lower(),
                raw_line=clean,
            )
        )
    return events


def parse_file(path: str | Path, *, strict: bool = False) -> list[RawEvent]:
    log_path = Path(path)
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        return parse_lines(handle, strict=strict)
