"""Unit tests for the alert receiver. Run: python3 -m unittest discover -s projects/03-llm-observability/alert-receiver"""
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import receiver

# The shape Alertmanager's webhook sends (version 4), trimmed to the fields that matter.
WEBHOOK = {
    "version": "4", "status": "firing", "receiver": "alert-receiver",
    "alerts": [
        {"status": "firing", "labels": {"alertname": "ChatDefaultFallbackHigh", "severity": "page"},
         "startsAt": "2026-10-20T10:00:00Z"},
        {"status": "resolved", "labels": {"alertname": "GatewayAvailabilityBudgetBurn", "sloth_severity": "ticket"},
         "startsAt": "2026-10-20T09:50:00Z"},
    ],
}


class AlertReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), receiver.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        receiver.LOG.clear()

    def post(self, data):
        req = urllib.request.Request(self.base + "/", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status

    def log(self):
        with urllib.request.urlopen(self.base + "/log", timeout=5) as r:
            return [json.loads(line) for line in r.read().decode().splitlines()]

    def test_one_line_per_alert_with_the_arrival_time(self):
        before = time.time()
        self.assertEqual(self.post(json.dumps(WEBHOOK).encode()), 200)
        lines = self.log()
        self.assertEqual([(e["alertname"], e["status"]) for e in lines],
                         [("ChatDefaultFallbackHigh", "firing"), ("GatewayAvailabilityBudgetBurn", "resolved")])
        self.assertEqual(lines[0]["labels"], {"alertname": "ChatDefaultFallbackHigh", "severity": "page"})
        self.assertEqual(lines[0]["startsAt"], "2026-10-20T10:00:00Z")
        self.assertTrue(before <= lines[0]["received_at"] <= time.time())

    def test_not_a_webhook_is_refused_and_not_recorded(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post(b"not json")
        self.assertEqual(e.exception.code, 400)
        self.assertEqual(self.log(), [])


if __name__ == "__main__":
    unittest.main()
