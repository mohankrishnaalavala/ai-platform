"""Unit tests for demo.py's output helpers (no gateway needed)."""
import json
import unittest

import demo


class DemoTest(unittest.TestCase):
    def test_keys_hashes_and_team_ids_are_blanked(self):
        line = ("team payments (0b6f1c2e-3a4d-4e5f-8a9b-0c1d2e3f4a5b): budget $20; key sk-AbC_12.x-9 "
                "hash " + "a1" * 32)
        self.assertEqual(demo.clean(line), "team payments (<team id>): budget $20; key sk-<redacted> hash <hash>")

    def test_a_stream_gives_its_text(self):
        events = [{"model": "gemini-3.5-flash-lite", "choices": [{"delta": {"role": "assistant", "content": ""}}]},
                  {"model": "gemini-3.5-flash-lite", "choices": [{"delta": {"content": "Hello"}}]},
                  {"choices": [{"delta": {"content": " <EMAIL_ADDRESS>"}}]}, {"choices": [], "usage": {}}]
        raw = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        self.assertEqual(demo.sse(raw), "Hello <EMAIL_ADDRESS>")


if __name__ == "__main__":
    unittest.main()
