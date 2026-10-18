"""Smoke tests for a running gateway: in CI against tests/compose.yaml (mock providers), in a session
against the cluster through a port-forward. Skipped unless GATEWAY_URL and MASTER_KEY are set.
Keys are created for the test and deleted after it; no key and no response body is printed.
Run: GATEWAY_URL=http://127.0.0.1:4000 MASTER_KEY=... python3 -m unittest discover -s projects/02-llm-gateway/tests
"""
import json
import os
import unittest
import urllib.error
import urllib.request

URL = os.environ.get("GATEWAY_URL", "")
MASTER = os.environ.get("MASTER_KEY", "")


def call(method, path, key=None, body=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(URL + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, None  # the error body can quote part of the key: never kept


@unittest.skipUnless(URL and MASTER, "needs a running gateway (GATEWAY_URL, MASTER_KEY)")
class GatewaySmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status, body = call("POST", "/key/generate", MASTER, {"key_alias": "smoke-test"})
        assert status == 200, f"key/generate returned {status}"
        cls.key = body["key"]

    @classmethod
    def tearDownClass(cls):
        call("POST", "/key/delete", MASTER, {"keys": [cls.key]})

    def test_ready(self):
        self.assertEqual(call("GET", "/health/readiness")[0], 200)

    def test_both_aliases_are_listed(self):
        status, body = call("GET", "/v1/models", self.key)
        self.assertEqual(status, 200)
        self.assertLessEqual({"chat-default", "chat-hosted"}, {m["id"] for m in body["data"]})

    def test_a_call_without_a_key_is_rejected(self):
        status, _ = call("POST", "/v1/chat/completions", None, {"model": "chat-hosted", "messages": []})
        self.assertEqual(status, 401)

    def test_the_hosted_alias_answers(self):
        status, body = call("POST", "/v1/chat/completions", self.key, {
            "model": "chat-hosted", "max_tokens": 5,
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}]})
        self.assertEqual(status, 200)
        self.assertTrue(body["choices"][0]["message"]["content"])


if __name__ == "__main__":
    unittest.main()
