"""Canonicalization of raw monitor events."""

from __future__ import annotations

from collections.abc import Iterable

from .model import CanonicalEvent, RawEvent


class NormalizationError(ValueError):
    """Raised when a requested canonical trace cannot be produced."""


def normalize_events(
    events: Iterable[RawEvent],
    *,
    signal: str | None = None,
    pins: Iterable[int] | None = None,
    anchor: str = "first_event",
    require_events: bool = True,
) -> list[CanonicalEvent]:
    """Filter and canonicalize a transition stream.

    Relative cycles share a global origin, while ``delta_cycles`` is measured
    from the previous event on the same signal/index stream.
    """

    if anchor not in {"first_event", "zero"}:
        raise NormalizationError(f"unsupported anchor: {anchor!r}")
    pin_set = set(pins) if pins is not None else None
    selected = [
        event
        for event in events
        if (signal is None or event.signal == signal)
        and (pin_set is None or event.index in pin_set)
    ]
    if not selected:
        if require_events:
            selector = signal or "any signal"
            if pin_set is not None:
                selector += f" pins {sorted(pin_set)}"
            raise NormalizationError(f"no monitor events matched {selector}")
        return []

    origin = selected[0].cycle if anchor == "first_event" else 0
    previous_by_stream: dict[tuple[str, int], int] = {}
    canonical: list[CanonicalEvent] = []
    for sequence, event in enumerate(selected, start=1):
        key = (event.signal, event.index)
        previous = previous_by_stream.get(key)
        canonical.append(
            CanonicalEvent(
                sequence=sequence,
                line_no=event.line_no,
                signal=event.signal,
                index=event.index,
                old=event.old,
                new=event.new,
                raw_cycle=event.cycle,
                relative_cycle=event.cycle - origin,
                delta_cycles=None if previous is None else event.cycle - previous,
            )
        )
        previous_by_stream[key] = event.cycle
    return canonical
