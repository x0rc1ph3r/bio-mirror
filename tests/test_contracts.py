from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from biomirror.contracts import (
    ContractError,
    evaluate_contract,
    load_contract,
    selector_from_contract,
    validate_contract,
)
from biomirror.parser import parse_file, parse_lines


FIXTURES = Path(__file__).parent / "fixtures"


def compact_contract() -> dict:
    return {
        "version": 1,
        "name": "blink",
        "selector": {"signal": "gpio_out", "pin": 21},
        "timing_tolerance_cycles": 10,
        "invariants": {
            "alternation": True,
            "pulse_width_cycles": {
                "high": {"expected": 144, "min": 140, "max": 148},
                "low": {"expected": 167, "min": 163, "max": 171},
            },
            "min_interval_cycles": 100,
            "edge_count": {"min": 3, "max": 10},
            "allowed_pins": [21],
        },
    }


class ContractValidationTests(unittest.TestCase):
    def test_compact_contract_expands_to_canonical_form(self) -> None:
        contract = validate_contract(compact_contract())
        self.assertEqual(("gpio_out", [21], "first_event"), selector_from_contract(contract))
        self.assertEqual(6, len(contract["invariants"]))
        self.assertEqual("compact", contract["metadata"]["source_schema"])

    def test_loads_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text(json.dumps(compact_contract()), encoding="utf-8")
            self.assertEqual(1, load_contract(path)["version"])

    def test_invalid_json_has_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "line 1, column 2"):
                load_contract(path)

    def test_unknown_invariant_and_duplicate_ids_are_rejected(self) -> None:
        canonical = {
            "version": 1,
            "invariants": [{"id": "x", "type": "mystery", "signal": "gpio_out"}],
        }
        with self.assertRaisesRegex(ContractError, "unsupported"):
            validate_contract(canonical)
        duplicate = {
            "version": 1,
            "invariants": [
                {"id": "x", "type": "edge_count", "signal": "g", "pin": 1, "min": 1},
                {"id": "x", "type": "edge_count", "signal": "g", "pin": 1, "min": 1},
            ],
        }
        with self.assertRaisesRegex(ContractError, "duplicate"):
            validate_contract(duplicate)

    def test_bad_bounds_are_rejected(self) -> None:
        contract = compact_contract()
        contract["invariants"]["pulse_width_cycles"]["high"]["min"] = 149
        with self.assertRaisesRegex(ContractError, "must not exceed"):
            validate_contract(contract)


class ContractEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = validate_contract(compact_contract())
        self.good = parse_file(FIXTURES / "reference.log")
        self.fault = parse_file(FIXTURES / "fault.log")

    def test_all_invariants_pass_good_trace(self) -> None:
        results = evaluate_contract(self.contract, self.good)
        self.assertEqual({"pass"}, {result["status"] for result in results})

    def test_fault_fails_high_width_and_min_interval(self) -> None:
        results = {result["id"]: result for result in evaluate_contract(self.contract, self.fault)}
        self.assertEqual("fail", results["pulse-width-high"]["status"])
        self.assertEqual(74, results["pulse-width-high"]["evidence"]["sample"]["width_cycles"])
        self.assertEqual("fail", results["minimum-interval"]["status"])
        self.assertEqual("pass", results["pulse-width-low"]["status"])

    def test_alternation_detects_discontinuity_and_unknown(self) -> None:
        discontinuous = parse_lines(
            [
                "[mon] cyc=1 gpio_out[21]: 0 -> 1\n",
                "[mon] cyc=2 gpio_out[21]: 0 -> 1\n",
            ]
        )
        result = evaluate_contract(self.contract, discontinuous)[0]
        self.assertEqual("fail", result["status"])
        unknown = parse_lines(["[mon] cyc=1 gpio_out[21]: x -> 1\n"])
        self.assertIn("unknown state", evaluate_contract(self.contract, unknown)[0]["message"])

    def test_allowed_pins_sees_events_outside_selector(self) -> None:
        events = self.good + parse_lines(["[mon] cyc=400 gpio_out[22]: 0 -> 1\n"])
        allowed = evaluate_contract(self.contract, events)[-1]
        self.assertEqual("fail", allowed["status"])
        self.assertEqual("gpio_out[22]", allowed["evidence"]["first_violation"]["stream"])

    def test_incomplete_pulse_does_not_vacuously_pass(self) -> None:
        one_edge = parse_lines(["[mon] cyc=1 gpio_out[21]: 0 -> 1\n"])
        results = {result["id"]: result for result in evaluate_contract(self.contract, one_edge)}
        self.assertEqual("fail", results["pulse-width-high"]["status"])
        self.assertEqual("fail", results["pulse-width-low"]["status"])
        self.assertEqual("fail", results["minimum-interval"]["status"])

    def test_edge_count_enforces_maximum(self) -> None:
        contract = compact_contract()
        contract["invariants"]["edge_count"] = {"max": 2}
        result = {
            row["id"]: row
            for row in evaluate_contract(validate_contract(contract), self.good)
        }["edge-count"]
        self.assertEqual("fail", result["status"])


if __name__ == "__main__":
    unittest.main()
