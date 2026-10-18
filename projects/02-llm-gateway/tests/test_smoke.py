"""Smoke tests for a running gateway: in CI against tests/compose.yaml (mock providers), in a session
against the cluster through a port-forward. Skipped unless GATEWAY_URL and MASTER_KEY are set.
Keys are created for the test and deleted after it; no key and no error body is printed.
Run: GATEWAY_URL=http://127.0.0.1:4000 MASTER_KEY=... python3 -m unittest discover -s projects/02-llm-gateway/tests
"""
import unittest

from client import MASTER, URL, call, chat, delete_key, new_key


@unittest.skipUnless(URL and MASTER, "needs a running gateway (GATEWAY_URL, MASTER_KEY)")
class GatewaySmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = new_key("smoke-test")

    @classmethod
    def tearDownClass(cls):
        delete_key(cls.key)

    def test_ready(self):
        self.assertEqual(call("GET", "/health/readiness")[0], 200)

    def test_both_aliases_are_listed(self):
        status, body, _, _ = call("GET", "/v1/models", self.key)
        self.assertEqual(status, 200)
        self.assertLessEqual({"chat-default", "chat-hosted"}, {m["id"] for m in body["data"]})

    def test_a_call_without_a_key_is_rejected(self):
        self.assertEqual(chat(None, "chat-hosted", "hello")[0], 401)

    # In CI this also leaves an open connection to the vLLM mock, so the fallback test that follows
    # sees what a vLLM pod dying in use looks like (a hung connection), not just a missing host.
    def test_the_default_alias_answers(self):
        status, body, _, _ = chat(self.key, "chat-default", "Reply with the single word: ok")
        self.assertEqual(status, 200)
        self.assertTrue(body["choices"][0]["message"]["content"])

    def test_the_hosted_alias_answers(self):
        status, body, _, _ = chat(self.key, "chat-hosted", "Reply with the single word: ok")
        self.assertEqual(status, 200)
        self.assertTrue(body["choices"][0]["message"]["content"])


if __name__ == "__main__":
    unittest.main()
