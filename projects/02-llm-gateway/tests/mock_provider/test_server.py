"""Unit tests for the mock provider. Run: python3 -m unittest discover -s projects/02-llm-gateway/tests/mock_provider"""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import server


class MockProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def post(self, body):
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.headers["Content-Type"], r.read().decode()

    def log(self):
        with urllib.request.urlopen(self.base + "/log", timeout=5) as r:
            return [json.loads(line) for line in r.read().decode().splitlines()]

    def test_plain_reply_has_usage_and_the_body_is_recorded(self):
        ctype, body = self.post({"model": "m", "messages": [{"role": "user", "content": "plain-1"}]})
        reply = json.loads(body)
        self.assertEqual(ctype, "application/json")
        self.assertEqual(reply["choices"][0]["message"]["content"], f"reply from {server.NAME}")
        self.assertEqual(reply["usage"]["total_tokens"], 60)
        self.assertIn("plain-1", json.dumps([b["messages"] for b in self.log()]))

    def test_streaming_reply_ends_with_done_and_the_body_is_recorded(self):
        ctype, body = self.post({"model": "m", "stream": True, "messages": [{"role": "user", "content": "stream-1"}]})
        self.assertEqual(ctype, "text/event-stream")
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))
        self.assertIn("reply from", body)
        self.assertIn("stream-1", json.dumps([b["messages"] for b in self.log()]))


if __name__ == "__main__":
    unittest.main()
