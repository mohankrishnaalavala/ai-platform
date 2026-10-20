"""Unit tests for the time-to-alert calculator, on made-up receiver lines (P3's real alert names)."""
import unittest

from time_to_alert import table, time_to_alert

EVENTS = [
    {"experiment": "2-flood", "started_at": 1000, "expects": "VllmTtftBudgetBurn"},
    {"experiment": "1-vllm-killed", "started_at": 5000, "expects": "ChatDefaultFallbackHigh"},
    {"experiment": "3-hosted-blocked", "started_at": 6000, "expects": "GatewayAvailabilityBudgetBurn"},
]


def alert(at, name, status="firing"):
    return {"received_at": at, "status": status, "alertname": name, "labels": {}, "startsAt": ""}


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


if __name__ == "__main__":
    unittest.main()
