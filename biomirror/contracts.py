"""Declarative JSON contracts for observable GPIO behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import RawEvent


class ContractError(ValueError):
    """Raised when a contract is invalid or unsupported."""


_COMMON_FIELDS = {"id", "type", "signal"}
_TYPE_FIELDS = {
    "alternates": _COMMON_FIELDS | {"pin"},
    "pulse_width": _COMMON_FIELDS
    | {"pin", "state", "min_cycles", "max_cycles", "min_samples"},
    "min_interval": _COMMON_FIELDS | {"pin", "min_cycles"},
    "edge_count": _COMMON_FIELDS | {"pin", "min", "max"},
    "allowed_pins": _COMMON_FIELDS | {"pins"},
}


def _expand_compact_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Translate the demo-friendly contract shape into the canonical v1 form.

    The repository contract intentionally reads like a behavior specification;
    the verifier internally uses a flat invariant list. Supporting both keeps
    the command-line format pleasant without duplicating evaluator logic.
    """

    allowed_top = {
        "version",
        "name",
        "selector",
        "timing_tolerance_cycles",
        "invariants",
    }
    unknown = set(contract) - allowed_top
    if unknown:
        raise ContractError(f"unknown contract field(s): {', '.join(sorted(unknown))}")
    selector = contract.get("selector")
    if not isinstance(selector, dict):
        raise ContractError("selector must be an object")
    unknown_selector = set(selector) - {"signal", "pin", "pins", "anchor"}
    if unknown_selector:
        raise ContractError(
            f"unknown selector field(s): {', '.join(sorted(unknown_selector))}"
        )
    signal = selector.get("signal")
    if not isinstance(signal, str) or not signal:
        raise ContractError("selector.signal must be a non-empty string")
    if "pin" in selector and "pins" in selector:
        raise ContractError("selector must use pin or pins, not both")
    pins = selector.get("pins", [selector.get("pin")])
    if not isinstance(pins, list) or not pins or any(pin is None for pin in pins):
        raise ContractError("selector.pin or selector.pins is required")
    for pin in pins:
        _require_int(pin, "selector pin")

    source_invariants = contract.get("invariants")
    if not isinstance(source_invariants, dict):
        raise ContractError("compact invariants must be an object")
    unknown_invariants = set(source_invariants) - {
        "alternation",
        "pulse_width_cycles",
        "min_interval_cycles",
        "edge_count",
        "allowed_pins",
    }
    if unknown_invariants:
        raise ContractError(
            f"unknown invariant field(s): {', '.join(sorted(unknown_invariants))}"
        )

    expanded: list[dict[str, Any]] = []
    primary_pin = pins[0]
    alternation = source_invariants.get("alternation", False)
    if not isinstance(alternation, bool):
        raise ContractError("invariants.alternation must be a boolean")
    if alternation:
        expanded.append(
            {
                "id": "alternation",
                "type": "alternates",
                "signal": signal,
                "pin": primary_pin,
            }
        )

    pulse_widths = source_invariants.get("pulse_width_cycles", {})
    if not isinstance(pulse_widths, dict):
        raise ContractError("invariants.pulse_width_cycles must be an object")
    unknown_states = set(pulse_widths) - {"high", "low"}
    if unknown_states:
        raise ContractError(
            "unknown pulse-width state(s): " + ", ".join(sorted(unknown_states))
        )
    for label, state in (("high", "1"), ("low", "0")):
        if label not in pulse_widths:
            continue
        bounds = pulse_widths[label]
        if not isinstance(bounds, dict):
            raise ContractError(f"pulse_width_cycles.{label} must be an object")
        unknown_bounds = set(bounds) - {"expected", "min", "max", "min_samples"}
        if unknown_bounds:
            raise ContractError(
                f"unknown pulse_width_cycles.{label} field(s): "
                + ", ".join(sorted(unknown_bounds))
            )
        if "expected" in bounds:
            _require_int(bounds["expected"], f"pulse_width_cycles.{label}.expected")
        if "min" not in bounds or "max" not in bounds:
            raise ContractError(f"pulse_width_cycles.{label} requires min and max")
        expanded.append(
            {
                "id": f"pulse-width-{label}",
                "type": "pulse_width",
                "signal": signal,
                "pin": primary_pin,
                "state": state,
                "min_cycles": bounds["min"],
                "max_cycles": bounds["max"],
                "min_samples": bounds.get("min_samples", 1),
            }
        )

    if "min_interval_cycles" in source_invariants:
        expanded.append(
            {
                "id": "minimum-interval",
                "type": "min_interval",
                "signal": signal,
                "pin": primary_pin,
                "min_cycles": source_invariants["min_interval_cycles"],
            }
        )
    if "edge_count" in source_invariants:
        edge_count = source_invariants["edge_count"]
        if not isinstance(edge_count, dict):
            raise ContractError("invariants.edge_count must be an object")
        expanded.append(
            {
                "id": "edge-count",
                "type": "edge_count",
                "signal": signal,
                "pin": primary_pin,
                **edge_count,
            }
        )
    if "allowed_pins" in source_invariants:
        expanded.append(
            {
                "id": "allowed-pins",
                "type": "allowed_pins",
                "signal": signal,
                "pins": source_invariants["allowed_pins"],
            }
        )

    tolerance = contract.get("timing_tolerance_cycles", 0)
    return {
        "version": contract.get("version"),
        "trace": {
            "signal": signal,
            "pins": pins,
            "anchor": selector.get("anchor", "first_event"),
        },
        "comparison": {"tolerance_cycles": tolerance},
        "invariants": expanded,
        "metadata": {
            "name": contract.get("name"),
            "source_schema": "compact",
        },
    }


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{name} must be an integer >= {minimum}")
    return value


def _validate_selector(trace: Any) -> None:
    if not isinstance(trace, dict):
        raise ContractError("trace must be an object")
    unknown = set(trace) - {"signal", "pins", "anchor"}
    if unknown:
        raise ContractError(f"unknown trace field(s): {', '.join(sorted(unknown))}")
    if "signal" in trace and (not isinstance(trace["signal"], str) or not trace["signal"]):
        raise ContractError("trace.signal must be a non-empty string")
    if "pins" in trace:
        pins = trace["pins"]
        if not isinstance(pins, list) or not pins:
            raise ContractError("trace.pins must be a non-empty array")
        for pin in pins:
            _require_int(pin, "trace pin")
        if len(set(pins)) != len(pins):
            raise ContractError("trace.pins contains duplicates")
    if trace.get("anchor", "first_event") not in {"first_event", "zero"}:
        raise ContractError("trace.anchor must be 'first_event' or 'zero'")


def _validate_invariant(item: Any, position: int) -> None:
    prefix = f"invariants[{position}]"
    if not isinstance(item, dict):
        raise ContractError(f"{prefix} must be an object")
    kind = item.get("type")
    if kind not in _TYPE_FIELDS:
        raise ContractError(f"{prefix}.type is unsupported: {kind!r}")
    unknown = set(item) - _TYPE_FIELDS[kind]
    if unknown:
        raise ContractError(f"{prefix} has unknown field(s): {', '.join(sorted(unknown))}")
    if not isinstance(item.get("id"), str) or not item["id"]:
        raise ContractError(f"{prefix}.id must be a non-empty string")
    if not isinstance(item.get("signal"), str) or not item["signal"]:
        raise ContractError(f"{prefix}.signal must be a non-empty string")

    if kind == "allowed_pins":
        pins = item.get("pins")
        if not isinstance(pins, list) or not pins:
            raise ContractError(f"{prefix}.pins must be a non-empty array")
        for pin in pins:
            _require_int(pin, f"{prefix} pin")
        return

    _require_int(item.get("pin"), f"{prefix}.pin")
    if kind == "pulse_width":
        if item.get("state") not in {"0", "1"}:
            raise ContractError(f"{prefix}.state must be '0' or '1'")
        low = _require_int(item.get("min_cycles"), f"{prefix}.min_cycles")
        high = _require_int(item.get("max_cycles"), f"{prefix}.max_cycles")
        if low > high:
            raise ContractError(f"{prefix}.min_cycles must not exceed max_cycles")
        _require_int(item.get("min_samples", 1), f"{prefix}.min_samples", minimum=1)
    elif kind == "min_interval":
        _require_int(item.get("min_cycles"), f"{prefix}.min_cycles")
    elif kind == "edge_count":
        if "min" not in item and "max" not in item:
            raise ContractError(f"{prefix} must define min and/or max")
        low = _require_int(item.get("min", 0), f"{prefix}.min")
        high = _require_int(item["max"], f"{prefix}.max") if "max" in item else None
        if high is not None and low > high:
            raise ContractError(f"{prefix}.min must not exceed max")


def validate_contract(contract: Any) -> dict[str, Any]:
    if not isinstance(contract, dict):
        raise ContractError("contract must be a JSON object")
    if isinstance(contract.get("invariants"), dict) or "selector" in contract:
        contract = _expand_compact_contract(contract)
    unknown = set(contract) - {"version", "trace", "comparison", "invariants", "metadata"}
    if unknown:
        raise ContractError(f"unknown contract field(s): {', '.join(sorted(unknown))}")
    if contract.get("version") != 1:
        raise ContractError("only contract version 1 is supported")
    _validate_selector(contract.get("trace", {}))

    comparison = contract.get("comparison", {})
    if not isinstance(comparison, dict):
        raise ContractError("comparison must be an object")
    unknown_comparison = set(comparison) - {"tolerance_cycles"}
    if unknown_comparison:
        raise ContractError(
            f"unknown comparison field(s): {', '.join(sorted(unknown_comparison))}"
        )
    _require_int(comparison.get("tolerance_cycles", 0), "comparison.tolerance_cycles")

    invariants = contract.get("invariants")
    if not isinstance(invariants, list):
        raise ContractError("invariants must be an array")
    identifiers: set[str] = set()
    for position, item in enumerate(invariants):
        _validate_invariant(item, position)
        identifier = item["id"]
        if identifier in identifiers:
            raise ContractError(f"duplicate invariant id: {identifier!r}")
        identifiers.add(identifier)
    return contract


def load_contract(path: str | Path) -> dict[str, Any]:
    contract_path = Path(path)
    try:
        content = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(
            f"invalid JSON in {contract_path}: line {exc.lineno}, column {exc.colno}"
        ) from exc
    return validate_contract(content)


def selector_from_contract(contract: dict[str, Any]) -> tuple[str | None, list[int] | None, str]:
    trace = contract.get("trace", {})
    return trace.get("signal"), trace.get("pins"), trace.get("anchor", "first_event")


def tolerance_from_contract(contract: dict[str, Any]) -> int:
    return contract.get("comparison", {}).get("tolerance_cycles", 0)


def _stream_events(events: list[RawEvent], signal: str, pin: int) -> list[RawEvent]:
    return [event for event in events if event.signal == signal and event.index == pin]


def _result(item: dict[str, Any], passed: bool, message: str, **evidence: Any) -> dict[str, Any]:
    return {
        "id": item["id"],
        "type": item["type"],
        "status": "pass" if passed else "fail",
        "message": message,
        "evidence": evidence,
    }


def _evaluate_alternates(item: dict[str, Any], events: list[RawEvent]) -> dict[str, Any]:
    stream = _stream_events(events, item["signal"], item["pin"])
    if not stream:
        return _result(item, False, "no matching transition events", observed_events=0)
    previous: RawEvent | None = None
    for position, event in enumerate(stream, start=1):
        if event.old not in {"0", "1"} or event.new not in {"0", "1"}:
            return _result(
                item,
                False,
                f"event {position} contains unknown state {event.old}->{event.new}",
                event=event.as_dict(),
            )
        if event.old == event.new:
            return _result(
                item,
                False,
                f"event {position} is not a state change",
                event=event.as_dict(),
            )
        if previous is not None and previous.new != event.old:
            return _result(
                item,
                False,
                f"event {position} does not continue prior state {previous.new}",
                previous=previous.as_dict(),
                event=event.as_dict(),
            )
        previous = event
    return _result(item, True, f"{len(stream)} transitions alternate", observed_events=len(stream))


def _evaluate_pulse_width(item: dict[str, Any], events: list[RawEvent]) -> dict[str, Any]:
    stream = _stream_events(events, item["signal"], item["pin"])
    samples: list[dict[str, Any]] = []
    state = item["state"]
    for position, event in enumerate(stream[:-1]):
        if event.new == state:
            following = stream[position + 1]
            samples.append(
                {
                    "start_cycle": event.cycle,
                    "end_cycle": following.cycle,
                    "width_cycles": following.cycle - event.cycle,
                }
            )
    minimum_samples = item.get("min_samples", 1)
    if len(samples) < minimum_samples:
        return _result(
            item,
            False,
            f"only {len(samples)} complete pulse(s); need {minimum_samples}",
            samples=samples,
        )
    for position, sample in enumerate(samples, start=1):
        width = sample["width_cycles"]
        if not item["min_cycles"] <= width <= item["max_cycles"]:
            return _result(
                item,
                False,
                f"pulse {position} width {width} cycles is outside "
                f"[{item['min_cycles']}, {item['max_cycles']}]",
                sample=sample,
                observed_samples=len(samples),
            )
    return _result(
        item,
        True,
        f"{len(samples)} pulse width sample(s) within bounds",
        observed_samples=len(samples),
        widths_cycles=[sample["width_cycles"] for sample in samples],
    )


def _evaluate_min_interval(item: dict[str, Any], events: list[RawEvent]) -> dict[str, Any]:
    stream = _stream_events(events, item["signal"], item["pin"])
    if len(stream) < 2:
        return _result(
            item,
            False,
            "fewer than two transitions; interval cannot be established",
            observed_events=len(stream),
        )
    for position, (before, after) in enumerate(zip(stream, stream[1:]), start=1):
        interval = after.cycle - before.cycle
        if interval < item["min_cycles"]:
            return _result(
                item,
                False,
                f"interval {position} is {interval} cycles, below {item['min_cycles']}",
                before=before.as_dict(),
                after=after.as_dict(),
                interval_cycles=interval,
            )
    return _result(
        item,
        True,
        f"all {len(stream) - 1} intervals meet the minimum",
        observed_intervals=len(stream) - 1,
    )


def _evaluate_edge_count(item: dict[str, Any], events: list[RawEvent]) -> dict[str, Any]:
    count = len(_stream_events(events, item["signal"], item["pin"]))
    low = item.get("min", 0)
    high = item.get("max")
    passed = count >= low and (high is None or count <= high)
    bounds = f">= {low}" if high is None else f"[{low}, {high}]"
    return _result(
        item,
        passed,
        f"observed {count} edges; required {bounds}",
        observed_edges=count,
        minimum=low,
        maximum=high,
    )


def _evaluate_allowed_pins(item: dict[str, Any], events: list[RawEvent]) -> dict[str, Any]:
    allowed = set(item["pins"])
    violations = [
        event.as_dict()
        for event in events
        if event.signal == item["signal"] and event.index not in allowed
    ]
    if violations:
        first = violations[0]
        return _result(
            item,
            False,
            f"unexpected transition on {first['stream']}",
            first_violation=first,
            violation_count=len(violations),
        )
    return _result(
        item,
        True,
        f"all {item['signal']} transitions use allowed pins {sorted(allowed)}",
        allowed_pins=sorted(allowed),
    )


_EVALUATORS = {
    "alternates": _evaluate_alternates,
    "pulse_width": _evaluate_pulse_width,
    "min_interval": _evaluate_min_interval,
    "edge_count": _evaluate_edge_count,
    "allowed_pins": _evaluate_allowed_pins,
}


def evaluate_contract(contract: dict[str, Any], events: list[RawEvent]) -> list[dict[str, Any]]:
    """Evaluate all contract invariants without short-circuiting."""

    return [_EVALUATORS[item["type"]](item, events) for item in contract["invariants"]]
