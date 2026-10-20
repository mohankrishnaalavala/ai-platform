"""Unit tests for the time-to-alert calculator and the wait before each experiment (P3's real alert names)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

from time_to_alert import alert_names, record, table, time_to_alert

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = [
    {"experiment": "2-flood", "started_at": 1000, "expects": "VllmTtftBudgetBurn"},
    {"experiment": "1-vllm-killed", "started_at": 5000, "expects": "ChatDefaultFallbackHigh"},
    {"experiment": "3-hosted-blocked", "started_at": 6000, "expects": "GatewayAvailabilityBudgetBurn"},
]


def alert(at, name, status="firing"):
    return {"received_at": at, "status": status, "alertname": name, "labels": {}, "startsAt": ""}


def answer(*alerts):
    """Prometheus's answer to the query ALERTS, from (alertname, alertstate) pairs."""
    return {"status": "success", "data": {"result": [
        {"metric": {"__name__": "ALERTS", "alertname": n, "alertstate": st}, "value": [0, "1"]} for n, st in alerts]}}


class TimeToAlertTest(unittest.TestCase):
    def test_first_matching_firing_alert_per_experiment(self):
        rows = time_to_alert(EVENTS, [
            alert(900.0, "VllmTtftBudgetBurn"),                  # before the flood started: not counted
            alert(1450.2, "VllmTtftBudgetBurn", "resolved"),     # resolved, not firing
            alert(1480.0, "ChatDefaultFallbackHigh"),            # another alert during the flood
            alert(2380.4, "VllmTtftBudgetBurn"),                 # 23 min after the flood started
            alert(5630.0, "ChatDefaultFallbackHigh"),            # 10.5 min after the kill
            alert(6900.0, "GatewayAvailabilityBudgetBurn"),      # 15 min after the block
        ])
        self.assertEqual([(r[0], r[2], r[4]) for r in rows], [
            ("2-flood", "VllmTtftBudgetBurn", 23.0),
            ("1-vllm-killed", "ChatDefaultFallbackHigh", 10.5),
            ("3-hosted-blocked", "GatewayAvailabilityBudgetBurn", 15.0),
        ])

    def test_no_alert_is_x_not_a_number(self):
        rows = time_to_alert(EVENTS[:1], [alert(1200.0, "ChatDefaultFallbackHigh")])
        self.assertEqual(rows[0][2:], (None, None, None))
        self.assertIn("| [X] | [X] | [X] |", table(rows))

    def test_a_late_match_after_the_next_start_still_counts(self):
        rows = time_to_alert(EVENTS[:2], [alert(5300.0, "VllmTtftBudgetBurn")])
        self.assertEqual(rows[0][4], 71.7)


class WaitBeforeEachExperimentTest(unittest.TestCase):
    """gameday.sh waits only for the next experiment's own alert, pending or firing, not for every alert."""

    def clear(self, name, ans):
        return subprocess.run([sys.executable, os.path.join(HERE, "time_to_alert.py"), "clear", name],
                              input=json.dumps(ans), text=True, check=False).returncode == 0

    def test_waits_while_its_own_alert_is_pending_or_firing(self):
        self.assertFalse(self.clear("ChatDefaultFallbackHigh", answer(("ChatDefaultFallbackHigh", "pending"))))
        self.assertFalse(self.clear("ChatDefaultFallbackHigh", answer(("ChatDefaultFallbackHigh", "firing"))))

    def test_other_alerts_dont_hold_it_up(self):
        # Experiment 3 starts while vLLM is still down: the fallback alert keeps firing, as expected.
        self.assertTrue(self.clear("GatewayAvailabilityBudgetBurn",
                                   answer(("ChatDefaultFallbackHigh", "firing"), ("VllmTtftBudgetBurn", "pending"))))
        self.assertTrue(self.clear("VllmTtftBudgetBurn", answer()))  # Prometheus answered: no alert at all

    def test_expects_is_a_regex_as_in_time_to_alert(self):
        self.assertFalse(self.clear("fallback", answer(("ChatDefaultFallbackHigh", "pending"))))
        self.assertFalse(self.clear("Latency|Ttft", answer(("VllmTtftBudgetBurn", "firing"))))

    def test_a_failed_query_is_not_clear(self):
        for failed in ("", "not json", json.dumps({"status": "error", "error": "timeout"})):
            proc = subprocess.run([sys.executable, os.path.join(HERE, "time_to_alert.py"), "clear", "VllmTtftBudgetBurn"],
                                  input=failed, text=True, check=False)
            self.assertNotEqual(proc.returncode, 0, repr(failed))

    def test_records_what_fires_as_each_experiment_starts(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({"scrape_interval": "15s"}, f)
            ans = answer(("ChatDefaultFallbackHigh", "firing"), ("VllmTtftBudgetBurn", "pending"))
            record(path, "3-hosted-blocked", ans)
            with open(path) as f:
                settings = json.load(f)
        self.assertEqual(settings["firing_at_start"], {"3-hosted-blocked": ["ChatDefaultFallbackHigh"]})
        self.assertEqual(settings["scrape_interval"], "15s")
        self.assertEqual(alert_names(ans), ["ChatDefaultFallbackHigh", "VllmTtftBudgetBurn"])

    def test_a_failed_query_records_unknown_not_an_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                json.dump({}, f)
            record(path, "1-vllm-killed", {})
            with open(path) as f:
                self.assertTrue(json.load(f)["firing_at_start"]["1-vllm-killed"].startswith("unknown"))


if __name__ == "__main__":
    unittest.main()
