"""Unit tests for overhead.py. Run: python3 -m unittest discover -s projects/02-llm-gateway/bench"""
import unittest

import overhead


def run(ttft50=100.0, ttft95=200.0, e2e50=1000.0, e2e95=1500.0, out=1280, failed=0, config="0" * 16, c=10):
    # Synthetic results in the shape `vllm bench serve` saves, not measurements.
    return {"scenario": "chat", "max_concurrency": c, "config_hash": config, "failed": failed,
            "total_output_tokens": out, "p50_ttft_ms": ttft50, "p95_ttft_ms": ttft95,
            "p50_e2el_ms": e2e50, "p95_e2el_ms": e2e95}


class OverheadTest(unittest.TestCase):
    def test_the_added_latency_is_gateway_minus_direct(self):
        text = overhead.compare({("chat", 10): run()}, {("chat", 10): run(ttft50=112, ttft95=230, e2e50=1015, e2e95=1540)})
        self.assertIn("| chat | 10 | TTFT p50 | 100 | 112 | +12 |", text)
        self.assertIn("| chat | 10 | End to end p95 | 1500 | 1540 | +40 |", text)

    def test_different_output_lengths_make_end_to_end_not_comparable(self):
        text = overhead.compare({("chat", 10): run()}, {("chat", 10): run(ttft50=112, out=900)})
        self.assertIn("| chat | 10 | TTFT p50 | 100 | 112 | +12 |", text)
        self.assertIn("| chat | 10 | End to end p50 | 1000 | 1000 | [X] |", text)
        self.assertIn("output tokens differ", text)

    def test_failed_requests_different_configs_or_loads_are_refused(self):
        for d, g in [({("chat", 10): run()}, {("chat", 10): run(failed=2)}),
                     ({("chat", 10): run()}, {("chat", 10): run(config="1" * 16)}),
                     ({("chat", 10): run()}, {("chat", 50): run(c=50)})]:
            with self.assertRaises(SystemExit):
                overhead.compare(d, g)


if __name__ == "__main__":
    unittest.main()
