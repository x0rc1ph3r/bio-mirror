from __future__ import annotations

import unittest
from pathlib import Path

from biomirror.normalize import NormalizationError, normalize_events
from biomirror.parser import ParseError, parse_file, parse_lines


FIXTURES = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_parses_real_bio_sim_format_and_ignores_noise(self) -> None:
        events = parse_file(FIXTURES / "reference.log")
        self.assertEqual(4, len(events))
        self.assertEqual((47, "gpio_dir", 21, "0", "1"), (
            events[0].cycle,
            events[0].signal,
            events[0].index,
            events[0].old,
            events[0].new,
        ))

    def test_searches_interleaved_progress_text(self) -> None:
        events = parse_file(FIXTURES / "good.log")
        self.assertEqual([60, 204, 371], [event.cycle for event in events])

    def test_strips_ansi_and_accepts_crlf(self) -> None:
        lines = ["\x1b[32mstatus\x1b[0m [mon] cyc=7 gpio_out[2]: X -> 1\r\n"]
        events = parse_lines(lines)
        self.assertEqual(1, len(events))
        self.assertEqual(("x", "1"), (events[0].old, events[0].new))

    def test_equal_cycle_preserves_order(self) -> None:
        events = parse_lines(
            [
                "[mon] cyc=9 gpio_out[2]: 0 -> 1\n",
                "[mon] cyc=9 gpio_out[3]: 0 -> 1\n",
            ]
        )
        self.assertEqual([2, 3], [event.index for event in events])

    def test_cycle_regression_is_error(self) -> None:
        with self.assertRaisesRegex(ParseError, "moved backwards"):
            parse_lines(
                [
                    "[mon] cyc=10 gpio_out[2]: 0 -> 1\n",
                    "[mon] cyc=9 gpio_out[2]: 1 -> 0\n",
                ]
            )

    def test_strict_rejects_malformed_target_line(self) -> None:
        with self.assertRaisesRegex(ParseError, "malformed"):
            parse_lines(["[mon] cyc=nope gpio_out[2]: 0 -> 1\n"], strict=True)

    def test_lenient_ignores_malformed_target_line(self) -> None:
        self.assertEqual(
            [], parse_lines(["[mon] cyc=nope gpio_out[2]: 0 -> 1\n"], strict=False)
        )


class NormalizationTests(unittest.TestCase):
    def test_filters_and_anchors_first_event(self) -> None:
        raw = parse_file(FIXTURES / "reference.log")
        events = normalize_events(raw, signal="gpio_out", pins=[21])
        self.assertEqual([0, 144, 311], [event.relative_cycle for event in events])
        self.assertEqual([None, 144, 167], [event.delta_cycles for event in events])

    def test_zero_anchor_preserves_raw_cycles(self) -> None:
        raw = parse_file(FIXTURES / "reference.log")
        events = normalize_events(raw, signal="gpio_out", pins=[21], anchor="zero")
        self.assertEqual([58, 202, 369], [event.relative_cycle for event in events])

    def test_delta_is_per_stream(self) -> None:
        raw = parse_lines(
            [
                "[mon] cyc=10 gpio_out[1]: 0 -> 1\n",
                "[mon] cyc=12 gpio_out[2]: 0 -> 1\n",
                "[mon] cyc=20 gpio_out[1]: 1 -> 0\n",
            ]
        )
        events = normalize_events(raw, signal="gpio_out")
        self.assertEqual([None, None, 10], [event.delta_cycles for event in events])

    def test_empty_selection_can_be_required_or_allowed(self) -> None:
        raw = parse_file(FIXTURES / "reference.log")
        with self.assertRaisesRegex(NormalizationError, "no monitor events"):
            normalize_events(raw, signal="gpio_out", pins=[99])
        self.assertEqual(
            [],
            normalize_events(
                raw, signal="gpio_out", pins=[99], require_events=False
            ),
        )


if __name__ == "__main__":
    unittest.main()
