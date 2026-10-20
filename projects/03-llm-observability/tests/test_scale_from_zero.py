"""Unit tests for scale_from_zero.py's log parser, and that the KEDA trigger uses the query CI proves."""
import os
import unittest

from scale_from_zero import summarize
from test_metrics import SCALE_QUERY

HERE = os.path.dirname(os.path.abspath(__file__))


class SummaryTest(unittest.TestCase):
    def test_first_answer_and_self_hosted_again(self):
        s = summarize([
            "t=0.0s status=200 ms=812 fallbacks=1 model=gemini-3.5-flash-lite",
            "t=2.0s status=200 ms=700 fallbacks=1 model=gemini-3.5-flash-lite",
            "t=4.1s status=500 ms=31000 fallbacks=1 model=absent",
            "t=401.9s status=200 ms=950 fallbacks=0 model=Qwen/Qwen2.5-7B-Instruct",
        ])
        self.assertEqual((s["first_status"], s["first_ms"], s["first_fallbacks"]), (200, 812, "1"))
        self.assertEqual(s["self_hosted_after_s"], 401.9)
        self.assertEqual((s["answered"], s["probes"]), (3, 4))

    def test_never_self_hosted_is_none_not_a_number(self):
        s = summarize(["t=0.0s status=200 ms=800 fallbacks=1 model=g", "noise", "t=2.0s status=200 ms=1 fallbacks=absent model=g"])
        self.assertIsNone(s["self_hosted_after_s"])
        self.assertEqual(s["probes"], 2)

    def test_no_probes(self):
        self.assertIsNone(summarize(["nothing here"]))


class TriggerQueryTest(unittest.TestCase):
    def test_keda_trigger_uses_the_query_ci_checks(self):
        with open(os.path.join(HERE, "..", "helm", "vllm-scale-from-zero.yaml")) as f:
            self.assertIn(f"query: {SCALE_QUERY}\n", f.read())


if __name__ == "__main__":
    unittest.main()
