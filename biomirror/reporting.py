"""Stable machine-readable and concise terminal reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_report(
    *,
    comparison: dict[str, Any] | None,
    contract_results: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    comparison_passed = comparison is None or comparison["status"] == "pass"
    contracts_passed = all(result["status"] == "pass" for result in contract_results)
    return {
        "schema_version": 1,
        "status": "pass" if comparison_passed and contracts_passed else "fail",
        "comparison": comparison,
        "contracts": {
            "status": "pass" if contracts_passed else "fail",
            "passed": sum(result["status"] == "pass" for result in contract_results),
            "total": len(contract_results),
            "results": contract_results,
        },
        "provenance": provenance,
    }


def report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def write_json_report(path: str | Path, report: dict[str, Any]) -> None:
    Path(path).write_text(report_json(report), encoding="utf-8")


def _transition(event: dict[str, Any] | None) -> str:
    if event is None:
        return "EOF"
    timing: list[str] = []
    if event.get("raw_cycle") is not None:
        timing.append(f"raw={event['raw_cycle']}")
    timing.append(f"rel={event['relative_cycle']}")
    if event.get("delta_cycles") is not None:
        timing.append(f"interval={event['delta_cycles']}")
    return f"{event['stream']} {event['old']}->{event['new']} ({', '.join(timing)} cycles)"


def report_text(report: dict[str, Any]) -> str:
    lines = [f"BIO-Mirror {report['status'].upper()}"]
    comparison = report["comparison"]
    if comparison is not None:
        lines.append(
            "Golden comparison: "
            f"{comparison['status'].upper()} "
            f"({comparison['actual_events']}/{comparison['expected_events']} events, "
            f"tolerance +/-{comparison['tolerance_cycles']} cycles)"
        )
        divergence = comparison["first_divergence"]
        if divergence is not None:
            lines.append(
                f"First divergence: event {divergence['event_index']} "
                f"[{divergence['kind']}] {divergence['message']}"
            )
            lines.append(f"  expected: {_transition(divergence['expected'])}")
            lines.append(f"  actual:   {_transition(divergence['actual'])}")
            interval_delta = divergence.get("interval_delta_cycles")
            interval_percent = divergence.get("interval_delta_percent")
            if interval_delta is not None:
                percentage = (
                    f", {interval_percent:+.1f}%"
                    if interval_percent is not None
                    else ""
                )
                lines.append(
                    f"  interval difference: {interval_delta:+d} cycles{percentage}"
                )
            elif divergence.get("relative_cycle_delta") is not None:
                lines.append(
                    "  relative-time difference: "
                    f"{divergence['relative_cycle_delta']:+d} cycles"
                )

    contracts = report["contracts"]
    if contracts["total"]:
        lines.append(
            f"Contracts: {contracts['status'].upper()} "
            f"({contracts['passed']}/{contracts['total']} passed)"
        )
        for result in contracts["results"]:
            lines.append(
                f"  [{result['status'].upper()}] {result['id']}: {result['message']}"
            )

    files = report["provenance"].get("files", {})
    if files:
        lines.append("Provenance:")
        for name, record in files.items():
            lines.append(f"  {name}: {record['sha256'][:12]}  {record['path']}")
    return "\n".join(lines) + "\n"
