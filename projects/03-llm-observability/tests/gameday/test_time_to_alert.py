"""Unit tests for the time-to-alert calculator, on made-up receiver lines."""
import unittest

from time_to_alert import table, time_to_alert

EVENTS = [
    {"experiment": "2-flood", "started_at": 1000, "expects": "latency"},
    {"experiment": "1-vllm-killed", "started_at": 5000, "expects": "fallback"},
    {"experiment": "3-hosted-blocked", "started_at": 6000, "expects": "availability"},
]


def alert(at, name, status="firing"):
    return {"received_at": at, "status": status, "alertname": name, "labels": {}, "startsAt": ""}


class TimeToAlertTest(unittest.TestCase):
    def test_first_matching_firing_alert_per_experiment(self):
        rows = time_to_alert(EVENTS, [
            alert(900, "GatewayLatencyBurnFast"),            # before the flood started: not counted
            alert(1450, "GatewayLatencyBurnFast", "resolved"),  # resolved, not firing
            alert(1480, "SomethingElse"),
            alert(1540, "GatewayLatencyBurnFast"),            # 9 min after the flood
            alert(5630, "ChatDefaultFallbackHigh"),           # 10.5 min after the kill
            alert(6900, "GatewayAvailabilityBurnFast"),       # 15 min after the block
        ])
        self.assertEqual([(r[0], r[2], r[4]) for r in rows], [
            ("2-flood", "GatewayLatencyBurnFast", 9.0),
            ("1-vllm-killed", "ChatDefaultFallbackHigh", 10.5),
            ("3-hosted-blocked", "GatewayAvailabilityBurnFast", 15.0),
        ])

    def test_no_alert_is_x_not_a_number(self):
        rows = time_to_alert(EVENTS[:1], [alert(1200, "ChatDefaultFallbackHigh")])
        self.assertEqual(rows[0][2:], (None, None, None))
        self.assertIn("| [X] | [X] | [X] |", table(rows))

    def test_a_late_match_after_the_next_start_still_counts(self):
        rows = time_to_alert(EVENTS[:2], [alert(5300, "GatewayLatencyBurnSlow")])
        self.assertEqual(rows[0][4], 71.7)


if __name__ == "__main__":
    unittest.main()
