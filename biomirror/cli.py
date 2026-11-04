"""Command-line interface for BIO-Mirror."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .compare import compare_traces
from .contracts import (
    ContractError,
    evaluate_contract,
    load_contract,
    selector_from_contract,
    tolerance_from_contract,
)
from .normalize import NormalizationError, normalize_events
from .parser import ParseError, parse_file
from .provenance import collect_provenance
from .reporting import build_report, report_json, report_text, write_json_report


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--json-out", metavar="PATH", help="also write the JSON report")


def _add_parse_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strict", action="store_true", help="reject malformed monitor records")


def _add_provenance_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--firmware", metavar="PATH", help="firmware image to hash")
    parser.add_argument("--sim-config", metavar="PATH", help="simulator config to hash")
    parser.add_argument("--repo", metavar="PATH", help="Git repository for commit metadata")
    parser.add_argument("--container-image", help="container image tag or digest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="biomirror",
        description="Normalize and verify Baochip BIO simulator traces.",
    )
    parser.add_argument("--version", action="version", version=f"BIO-Mirror {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize_parser = subparsers.add_parser("normalize", help="print a canonical trace")
    normalize_parser.add_argument("log", metavar="LOG")
    normalize_parser.add_argument("--signal", help="monitor signal to select")
    normalize_parser.add_argument("--pin", type=int, action="append", dest="pins")
    normalize_parser.add_argument(
        "--anchor", choices=("first_event", "zero"), default="first_event"
    )
    normalize_parser.add_argument("--json", action="store_true", help="print JSON")
    _add_parse_arguments(normalize_parser)

    compare_parser = subparsers.add_parser("compare", help="compare actual and golden traces")
    compare_parser.add_argument("reference", metavar="REFERENCE")
    compare_parser.add_argument("actual", metavar="ACTUAL")
    compare_parser.add_argument("--contract", metavar="PATH")
    compare_parser.add_argument("--signal")
    compare_parser.add_argument("--pin", type=int, action="append", dest="pins")
    compare_parser.add_argument("--tolerance-cycles", type=int)
    _add_output_arguments(compare_parser)
    _add_parse_arguments(compare_parser)
    _add_provenance_arguments(compare_parser)

    check_parser = subparsers.add_parser("check", help="check a trace against a contract")
    check_parser.add_argument("actual", metavar="ACTUAL")
    check_parser.add_argument("--contract", required=True, metavar="PATH")
    _add_output_arguments(check_parser)
    _add_parse_arguments(check_parser)
    _add_provenance_arguments(check_parser)
    return parser


def _print_normalized(args: argparse.Namespace) -> int:
    raw = parse_file(args.log, strict=args.strict)
    events = normalize_events(
        raw,
        signal=args.signal,
        pins=args.pins,
        anchor=args.anchor,
    )
    if args.json:
        import json

        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": str(Path(args.log).resolve()),
                    "events": [event.as_dict() for event in events],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for event in events:
            delta = "-" if event.delta_cycles is None else str(event.delta_cycles)
            print(
                f"{event.sequence:4d} rel={event.relative_cycle:<10d} "
                f"delta={delta:<10} raw={event.raw_cycle:<10d} "
                f"{event.stream} {event.old}->{event.new}"
            )
    return 0


def _resolve_selection(
    contract: dict[str, Any] | None,
    signal_override: str | None = None,
    pins_override: list[int] | None = None,
) -> tuple[str | None, list[int] | None, str]:
    if contract is None:
        return signal_override or "gpio_out", pins_override, "first_event"
    signal, pins, anchor = selector_from_contract(contract)
    return signal_override or signal or "gpio_out", pins_override or pins, anchor


def _provenance_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "firmware": getattr(args, "firmware", None),
        "sim_config": getattr(args, "sim_config", None),
        "repo": getattr(args, "repo", None),
        "container_image": getattr(args, "container_image", None),
    }


def _emit_report(args: argparse.Namespace, report: dict[str, Any]) -> int:
    if args.json_out:
        write_json_report(args.json_out, report)
    output = report_json(report) if args.json else report_text(report)
    print(output, end="")
    return 0 if report["status"] == "pass" else 1


def _compare(args: argparse.Namespace) -> int:
    contract = load_contract(args.contract) if args.contract else None
    signal, pins, anchor = _resolve_selection(contract, args.signal, args.pins)
    tolerance = (
        args.tolerance_cycles
        if args.tolerance_cycles is not None
        else tolerance_from_contract(contract) if contract is not None else 0
    )
    if tolerance < 0:
        raise ValueError("--tolerance-cycles must be non-negative")

    reference_raw = parse_file(args.reference, strict=args.strict)
    actual_raw = parse_file(args.actual, strict=args.strict)
    reference = normalize_events(
        reference_raw, signal=signal, pins=pins, anchor=anchor, require_events=True
    )
    actual = normalize_events(
        actual_raw, signal=signal, pins=pins, anchor=anchor, require_events=False
    )
    comparison = compare_traces(reference, actual, tolerance_cycles=tolerance)
    contract_results = evaluate_contract(contract, actual_raw) if contract is not None else []
    provenance = collect_provenance(
        reference=args.reference,
        actual=args.actual,
        contract=args.contract,
        **_provenance_kwargs(args),
    )
    return _emit_report(
        args,
        build_report(
            comparison=comparison,
            contract_results=contract_results,
            provenance=provenance,
        ),
    )


def _check(args: argparse.Namespace) -> int:
    contract = load_contract(args.contract)
    raw = parse_file(args.actual, strict=args.strict)
    contract_results = evaluate_contract(contract, raw)
    provenance = collect_provenance(
        actual=args.actual,
        contract=args.contract,
        **_provenance_kwargs(args),
    )
    return _emit_report(
        args,
        build_report(
            comparison=None,
            contract_results=contract_results,
            provenance=provenance,
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "normalize":
            return _print_normalized(args)
        if args.command == "compare":
            return _compare(args)
        if args.command == "check":
            return _check(args)
        parser.error(f"unknown command: {args.command}")
    except (OSError, ParseError, NormalizationError, ContractError, ValueError) as exc:
        print(f"BIO-Mirror ERROR: {exc}", file=sys.stderr)
        return 2
    return 2
