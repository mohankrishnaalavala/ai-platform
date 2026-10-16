"""Smoke tests for the vLLM server. tests/run.sh runs them with VLLM_URL set (through a port-forward).

Without VLLM_URL they are skipped, so CI's unit-test discovery needs no cluster.
"""
import json
import os
import unittest
import urllib.request

URL = os.environ.get("VLLM_URL")
MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-7B-Instruct")


def call(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


@unittest.skipUnless(URL, "VLLM_URL is not set (no session cluster)")
class SmokeTest(unittest.TestCase):
    def test_models_lists_the_model(self):
        self.assertIn(MODEL, [m["id"] for m in call("/v1/models")["data"]])

    def test_chat_completion_returns_text(self):
        out = call("/v1/chat/completions", {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "max_tokens": 16,
        })
        self.assertTrue(out["choices"][0]["message"]["content"].strip())


if __name__ == "__main__":
    unittest.main()
