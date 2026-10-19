"""Streaming: the gateway must pass a stream on as it arrives, not hold it until the answer is done.
In session 3's overhead run every streamed answer reached the client as one piece (inter-token
latency 0, time to first token equal to the whole answer). CI's mock vLLM sends its events
STREAM_DELAY_S apart (compose.yaml); in a session, vLLM streams for real.
Skipped unless GATEWAY_URL and MASTER_KEY are set, and when the answer came from the fallback.
"""
import json
import time
import unittest
import urllib.request

from client import MASTER, URL, delete_key, new_key

MIN_SPREAD_S = 0.3  # first to last piece of content; a buffered stream arrives all at once


@unittest.skipUnless(URL and MASTER, "needs a running gateway (GATEWAY_URL, MASTER_KEY)")
class StreamingTest(unittest.TestCase):
    def test_a_stream_arrives_in_pieces(self):
        key = new_key("streaming-test")
        try:
            req = urllib.request.Request(
                URL + "/v1/chat/completions", method="POST",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                data=json.dumps({"model": "chat-default", "stream": True, "max_tokens": 50, "messages": [
                    {"role": "user", "content": "Count from 1 to 40, separated by spaces."}]}).encode())
            start, times = time.monotonic(), []
            with urllib.request.urlopen(req, timeout=60) as r:
                fallbacks = r.headers.get("x-litellm-attempted-fallbacks", "0")
                for line in r:  # each line as it arrives
                    line = line.decode().strip()
                    if line.startswith("data: {"):
                        choices = json.loads(line[len("data: "):]).get("choices") or [{}]
                        if choices[0].get("delta", {}).get("content"):
                            times.append(time.monotonic() - start)
        finally:
            delete_key(key)
        if fallbacks != "0":
            self.skipTest("the fallback answered; this checks the self-hosted route")
        print(f"\nstream: {len(times)} pieces of content, first at {times[0] * 1000:.0f} ms, "
              f"last at {times[-1] * 1000:.0f} ms" if times else "\nstream: no content")
        self.assertGreaterEqual(len(times), 2)
        self.assertGreaterEqual(times[-1] - times[0], MIN_SPREAD_S)


if __name__ == "__main__":
    unittest.main()
