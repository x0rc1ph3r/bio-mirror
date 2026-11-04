from __future__ import annotations

import unittest
from pathlib import Path

from biomirror.compare import compare_traces
from biomirror.normalize import normalize_events
from biomirror.parser import parse_file, parse_lines


FIXTURES = Path(__file__).parent / "fixtures"


def canonical(lines: list[str]):
    return normalize_events(parse_lines(lines), signal="gpio_out", pins=[21])


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = normalize_events(
            parse_file(FIXTURES / "reference.log"), signal="gpio_out", pins=[21]
        )

    def test_independently_shifted_good_run_passes(self) -> None:
        good = normalize_events(
            parse_file(FIXTURES / "good.log"), signal="gpio_out", pins=[21]
        )
        result = compare_traces(self.reference, good)
        self.assertEqual("pass", result["status"])

    def test_tolerance_boundary_is_inclusive(self) -> None:
        actual = canonical(
            [
                "[mon] cyc=100 gpio_out[21]: 0 -> 1\n",
                "[mon] cyc=249 gpio_out[21]: 1 -> 0\n",
                "[mon] cyc=411 gpio_out[21]: 0 -> 1\n",
            ]
        )
        self.assertEqual(
            "pass", compare_traces(self.reference, actual, tolerance_cycles=5)["status"]
        )

    def test_fault_finds_first_timing_divergence(self) -> None:
        fault = normalize_events(
            parse_file(FIXTURES / "fault.log"), signal="gpio_out", pins=[21]
        )
        result = compare_traces(self.reference, fault, tolerance_cycles=10)
        divergence = result["first_divergence"]
        self.assertEqual("fail", result["status"])
        self.assertEqual("timing_early", divergence["kind"])
        self.assertEqual(2, divergence["event_index"])
        self.assertEqual(-70, divergence["delta_cycles"])
        self.assertEqual(-70, divergence["interval_delta_cycles"])
        self.assertAlmostEqual(-48.6111, divergence["interval_delta_percent"], places=3)
        self.assertTrue(divergence["context"]["expected"])

    def test_transition_mismatch_precedes_timing(self) -> None:
        actual = canonical(
            [
                "[mon] cyc=58 gpio_out[21]: 0 -> 1\n",
                "[mon] cyc=202 gpio_out[21]: 0 -> 1\n",
                "[mon] cyc=369 gpio_out[21]: 0 -> 1\n",
            ]
        )
        divergence = compare_traces(self.reference, actual)["first_divergence"]
        self.assertEqual("transition", divergence["kind"])
        self.assertEqual(2, divergence["event_index"])

    def test_wrong_stream_is_reported(self) -> None:
        actual = normalize_events(
            parse_lines(["[mon] cyc=58 gpio_out[22]: 0 -> 1\n"]),
            signal="gpio_out",
        )
        divergence = compare_traces(self.reference, actual)["first_divergence"]
        self.assertEqual("signal", divergence["kind"])

    def test_missing_and_extra_events(self) -> None:
        missing = compare_traces(self.reference, self.reference[:2])
        extra = compare_traces(self.reference[:2], self.reference)
        self.assertEqual("missing_event", missing["first_divergence"]["kind"])
        self.assertEqual("extra_event", extra["first_divergence"]["kind"])

    def test_empty_actual_is_missing_event(self) -> None:
        result = compare_traces(self.reference, [])
        self.assertEqual("missing_event", result["first_divergence"]["kind"])
        self.assertIsNone(result["first_divergence"]["actual"])

    def test_negative_tolerance_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            compare_traces(self.reference, self.reference, tolerance_cycles=-1)


if __name__ == "__main__":
    unittest.main()
