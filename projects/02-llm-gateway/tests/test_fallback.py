"""Fallback: with the self-hosted model down, chat-default must still answer, through chat-hosted.
CI: after `docker compose stop mock-vllm`. Session: with vLLM scaled to 0, or not deployed at all.
Skipped unless GATEWAY_URL, MASTER_KEY and VLLM_DOWN=1 are set. Prints each request's latency.
"""
import os
import unittest

from client import MASTER, URL, chat, delete_key, new_key

TIMEOUT_S = 30         # chat-default's `timeout` in helm/gateway/values.yaml
STREAM_TIMEOUT_S = 10  # its `stream_timeout`
SLACK_S = 5            # more than this past the timeout means a silent retry or a hang


@unittest.skipUnless(URL and MASTER and os.environ.get("VLLM_DOWN") == "1",
                     "needs a running gateway with the self-hosted model down (VLLM_DOWN=1)")
class FallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = new_key("fallback-test")

    @classmethod
    def tearDownClass(cls):
        delete_key(cls.key)

    def check(self, stream):
        status, body, headers, seconds = chat(self.key, "chat-default", "Reply with the single word: ok", stream)
        print(f"\nfallback ({'stream' if stream else 'plain'}): status {status}, {seconds * 1000:.0f} ms, "
              f"attempted fallbacks {headers.get('x-litellm-attempted-fallbacks', 'absent')}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("x-litellm-attempted-fallbacks"), "1")
        self.assertLess(seconds, (STREAM_TIMEOUT_S if stream else TIMEOUT_S) + SLACK_S)
        if stream:
            self.assertIn("data: [DONE]", body)
        else:
            self.assertTrue(body["choices"][0]["message"]["content"])

    def test_plain_request_falls_back(self):
        self.check(stream=False)

    def test_streaming_request_falls_back(self):
        self.check(stream=True)


if __name__ == "__main__":
    unittest.main()
