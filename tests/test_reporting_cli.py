from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from biomirror.cli import main
from biomirror.provenance import collect_provenance, hash_file
from biomirror.reporting import build_report, report_json, report_text


FIXTURES = Path(__file__).parent / "fixtures"


def contract_data() -> dict:
    return {
        "version": 1,
        "selector": {"signal": "gpio_out", "pin": 21},
        "timing_tolerance_cycles": 10,
        "invariants": {
            "alternation": True,
            "pulse_width_cycles": {
                "high": {"expected": 144, "min": 140, "max": 148},
                "low": {"expected": 167, "min": 163, "max": 171},
            },
            "min_interval_cycles": 100,
            "edge_count": {"min": 3},
            "allowed_pins": [21],
        },
    }


class ProvenanceAndReportTests(unittest.TestCase):
    def test_hashes_input_bytes(self) -> None:
        record = hash_file(FIXTURES / "fault.log")
        expected = hashlib.sha256((FIXTURES / "fault.log").read_bytes()).hexdigest()
        self.assertEqual(expected, record["sha256"])
        self.assertGreater(record["size_bytes"], 0)

    def test_provenance_has_runtime_and_optional_container(self) -> None:
        provenance = collect_provenance(
            actual=FIXTURES / "fault.log", container_image="example@sha256:abc"
        )
        self.assertEqual("BIO-Mirror", provenance["tool"]["name"])
        self.assertEqual("example@sha256:abc", provenance["container_image"])
        self.assertIn("actual", provenance["files"])

    def test_text_and_json_show_first_divergence(self) -> None:
        comparison = {
            "status": "fail",
            "expected_events": 3,
            "actual_events": 3,
            "tolerance_cycles": 10,
            "first_divergence": {
                "event_index": 2,
                "kind": "timing_early",
                "message": "70 cycles early",
                "interval_delta_cycles": -70,
                "interval_delta_percent": -48.6111,
                "expected": {
                    "stream": "gpio_out[21]", "old": "1", "new": "0", "relative_cycle": 144
                },
                "actual": {
                    "stream": "gpio_out[21]", "old": "1", "new": "0", "relative_cycle": 74
                },
            },
        }
        report = build_report(comparison=comparison, contract_results=[], provenance={"files": {}})
        self.assertIn("First divergence: event 2", report_text(report))
        self.assertIn("interval difference: -70 cycles, -48.6%", report_text(report))
        self.assertEqual("fail", json.loads(report_json(report))["status"])


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.contract = Path(self.temporary.name) / "contract.json"
        self.contract.write_text(json.dumps(contract_data()), encoding="utf-8")

    def run_cli(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_normalize_outputs_canonical_json(self) -> None:
        code, output, error = self.run_cli(
            [
                "normalize", str(FIXTURES / "reference.log"),
                "--signal", "gpio_out", "--pin", "21", "--json",
            ]
        )
        self.assertEqual((0, ""), (code, error))
        payload = json.loads(output)
        self.assertEqual([0, 144, 311], [row["relative_cycle"] for row in payload["events"]])

    def test_compare_good_returns_zero_and_writes_json(self) -> None:
        report_path = Path(self.temporary.name) / "report.json"
        code, output, error = self.run_cli(
            [
                "compare", str(FIXTURES / "reference.log"), str(FIXTURES / "good.log"),
                "--contract", str(self.contract), "--json-out", str(report_path),
            ]
        )
        self.assertEqual((0, ""), (code, error))
        self.assertIn("BIO-Mirror PASS", output)
        self.assertEqual("pass", json.loads(report_path.read_text())["status"])

    def test_compare_fault_returns_one_with_exact_divergence(self) -> None:
        code, output, error = self.run_cli(
            [
                "compare", str(FIXTURES / "reference.log"), str(FIXTURES / "fault.log"),
                "--contract", str(self.contract),
            ]
        )
        self.assertEqual((1, ""), (code, error))
        self.assertIn("event 2 [timing_early]", output)
        self.assertIn("70 cycles early", output)
        self.assertIn("raw=202, rel=144, interval=144", output)
        self.assertIn("raw=132, rel=74, interval=74", output)
        self.assertIn("interval difference: -70 cycles, -48.6%", output)
        self.assertIn("pulse-width-high", output)

    def test_check_pass_and_fail(self) -> None:
        good = self.run_cli(
            ["check", str(FIXTURES / "reference.log"), "--contract", str(self.contract)]
        )
        fault = self.run_cli(
            ["check", str(FIXTURES / "fault.log"), "--contract", str(self.contract)]
        )
        self.assertEqual(0, good[0])
        self.assertEqual(1, fault[0])

    def test_bad_contract_returns_two(self) -> None:
        self.contract.write_text("{bad", encoding="utf-8")
        code, output, error = self.run_cli(
            ["check", str(FIXTURES / "reference.log"), "--contract", str(self.contract)]
        )
        self.assertEqual(2, code)
        self.assertEqual("", output)
        self.assertIn("BIO-Mirror ERROR", error)

    def test_missing_file_returns_two(self) -> None:
        code, _, error = self.run_cli(["normalize", "does-not-exist.log"])
        self.assertEqual(2, code)
        self.assertIn("BIO-Mirror ERROR", error)


if __name__ == "__main__":
    unittest.main()
