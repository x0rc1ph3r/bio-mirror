"""Golden-trace comparison and first-divergence localization."""

from __future__ import annotations

from typing import Any

from .model import CanonicalEvent


def _event_dict(event: CanonicalEvent | None) -> dict[str, Any] | None:
    return None if event is None else event.as_dict()


def _context(events: list[CanonicalEvent], index: int, radius: int = 1) -> list[dict[str, Any]]:
    start = max(0, index - radius)
    stop = min(len(events), index + radius + 1)
    return [event.as_dict() for event in events[start:stop]]


def compare_traces(
    expected: list[CanonicalEvent],
    actual: list[CanonicalEvent],
    *,
    tolerance_cycles: int = 0,
) -> dict[str, Any]:
    """Compare canonical event sequences and identify the first divergence."""

    if tolerance_cycles < 0:
        raise ValueError("tolerance_cycles must be non-negative")

    divergence: dict[str, Any] | None = None
    shared = min(len(expected), len(actual))
    for index in range(shared):
        want = expected[index]
        got = actual[index]
        kind: str | None = None
        message: str | None = None
        delta: int | None = None

        if want.stream != got.stream:
            kind = "signal"
            message = f"expected {want.stream}, observed {got.stream}"
        elif (want.old, want.new) != (got.old, got.new):
            kind = "transition"
            message = (
                f"expected {want.old}->{want.new}, "
                f"observed {got.old}->{got.new} on {want.stream}"
            )
        else:
            delta = got.relative_cycle - want.relative_cycle
            if abs(delta) > tolerance_cycles:
                kind = "timing_late" if delta > 0 else "timing_early"
                direction = "late" if delta > 0 else "early"
                message = (
                    f"{want.stream} {want.old}->{want.new} was "
                    f"{abs(delta)} cycles {direction}"
                )

        if kind is not None:
            interval_delta: int | None = None
            interval_percent: float | None = None
            if want.delta_cycles is not None and got.delta_cycles is not None:
                interval_delta = got.delta_cycles - want.delta_cycles
                if want.delta_cycles != 0:
                    interval_percent = interval_delta * 100.0 / want.delta_cycles
            divergence = {
                "kind": kind,
                "event_index": index + 1,
                "message": message,
                "delta_cycles": delta,
                "relative_cycle_delta": delta,
                "interval_delta_cycles": interval_delta,
                "interval_delta_percent": interval_percent,
                "expected": _event_dict(want),
                "actual": _event_dict(got),
                "context": {
                    "expected": _context(expected, index),
                    "actual": _context(actual, index),
                },
            }
            break

    if divergence is None and len(expected) != len(actual):
        index = shared
        missing = len(actual) < len(expected)
        want = expected[index] if index < len(expected) else None
        got = actual[index] if index < len(actual) else None
        kind = "missing_event" if missing else "extra_event"
        message = (
            f"actual trace ended before expected event {index + 1}"
            if missing
            else f"unexpected actual event {index + 1}"
        )
        divergence = {
            "kind": kind,
            "event_index": index + 1,
            "message": message,
            "delta_cycles": None,
            "relative_cycle_delta": None,
            "interval_delta_cycles": None,
            "interval_delta_percent": None,
            "expected": _event_dict(want),
            "actual": _event_dict(got),
            "context": {
                "expected": _context(expected, min(index, max(0, len(expected) - 1))),
                "actual": _context(actual, min(index, max(0, len(actual) - 1))),
            },
        }

    return {
        "status": "pass" if divergence is None else "fail",
        "expected_events": len(expected),
        "actual_events": len(actual),
        "tolerance_cycles": tolerance_cycles,
        "first_divergence": divergence,
    }
