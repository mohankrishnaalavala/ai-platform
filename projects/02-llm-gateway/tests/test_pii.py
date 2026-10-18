"""PII masking: none of the 20 fake-PII prompts (pii_prompts.json) may reach a provider, or any log, raw.
The mock provider records every request body, so the test reads what left the gateway. Each class
runs at its own point in CI (ci.yml, job gateway); each prints its misses per PII type.
- DirectMasking (MOCK_HOSTED_URL): every prompt to the hosted alias, plain and streaming.
- FallbackMasking (MOCK_HOSTED_URL, VLLM_DOWN=1): every prompt to chat-default, which falls back.
- NoRawPiiInLogs (PII_DUMP_FILE): the gateway's logs, traces (printed in full) and spend logs, dumped to that file.
- FailClosed (MOCK_HOSTED_URL, PRESIDIO_DOWN=1): with the analyzer down, a request is refused, not sent.
In a session HOSTED_ALIAS names the in-cluster mock's alias. Needs GATEWAY_URL and MASTER_KEY too.
"""
import json
import os
import unittest
import urllib.request
from collections import Counter

from client import MASTER, URL, chat, delete_key, new_key

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "pii_prompts.json")) as f:
    PROMPTS = json.load(f)["prompts"]
MOCK = os.environ.get("MOCK_HOSTED_URL", "")
HOSTED = os.environ.get("HOSTED_ALIAS", "chat-hosted")
VLLM_DOWN = os.environ.get("VLLM_DOWN") == "1"
PRESIDIO_DOWN = os.environ.get("PRESIDIO_DOWN") == "1"
GATEWAY = bool(URL and MASTER)


def received(tag):
    """The message text of every request the mock got that carries this tag."""
    with urllib.request.urlopen(MOCK + "/log", timeout=10) as r:
        bodies = [json.loads(line) for line in r.read().decode().splitlines() if line]
    texts = [json.dumps(b.get("messages", "")) for b in bodies]
    return [t for t in texts if f"[{tag}]" in t]


def misses(texts_by_prompt):
    """Raw values seen, per PII type, and how many values were sent per type."""
    raw, sent = Counter(), Counter()
    for p, texts in texts_by_prompt:
        for kind, values in p["pii"].items():
            for v in values:
                sent[kind] += 1
                if any(v in t for t in texts):
                    raw[kind] += 1
    return raw, sent


def report(name, raw, sent):
    print(f"\n{name}: raw values that got through, per type: "
          + ", ".join(f"{k} {raw[k]}/{sent[k]}" for k in sorted(sent)))


class _Keyed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = new_key(cls.__name__.lower())

    @classmethod
    def tearDownClass(cls):
        delete_key(cls.key)


@unittest.skipUnless(GATEWAY and MOCK and not VLLM_DOWN and not PRESIDIO_DOWN, "needs the gateway and MOCK_HOSTED_URL")
class DirectMasking(_Keyed):
    def test_no_raw_pii_reaches_the_hosted_provider(self):
        seen = []
        for stream in (False, True):
            for p in PROMPTS:
                tag = f"pii-{p['id']}-{'stream' if stream else 'plain'}"
                status = chat(self.key, HOSTED, f"[{tag}] {p['text']}", stream, max_tokens=5)[0]
                self.assertEqual(status, 200, tag)
                texts = received(tag)
                self.assertEqual(len(texts), 1, f"{tag}: the provider got {len(texts)} requests")
                seen.append((p, texts))
        raw, sent = misses(seen)
        report("direct (plain and streaming)", raw, sent)
        self.assertEqual(sum(raw.values()), 0)


@unittest.skipUnless(GATEWAY and MOCK and VLLM_DOWN and not PRESIDIO_DOWN, "needs MOCK_HOSTED_URL and VLLM_DOWN=1")
class FallbackMasking(_Keyed):
    def test_no_raw_pii_reaches_the_hosted_provider_on_fallback(self):
        seen = []
        for p in PROMPTS:
            tag = f"pii-{p['id']}-fallback"
            status, _, headers, _ = chat(self.key, "chat-default", f"[{tag}] {p['text']}", stream=True, max_tokens=5)
            self.assertEqual(status, 200, tag)
            self.assertEqual(headers.get("x-litellm-attempted-fallbacks"), "1", tag)
            texts = received(tag)
            self.assertEqual(len(texts), 1, f"{tag}: the provider got {len(texts)} requests")
            seen.append((p, texts))
        raw, sent = misses(seen)
        report("fallback (chat-default to the hosted alias)", raw, sent)
        self.assertEqual(sum(raw.values()), 0)


@unittest.skipUnless(os.environ.get("PII_DUMP_FILE"), "needs PII_DUMP_FILE (the gateway's logs, traces, spend logs)")
class NoRawPiiInLogs(unittest.TestCase):
    def test_no_raw_pii_in_logs_traces_or_spend_logs(self):
        with open(os.environ["PII_DUMP_FILE"]) as f:
            dump = f.read()
        self.assertGreater(len(dump), 0, "the dump file is empty")
        raw, sent = misses([(p, [dump]) for p in PROMPTS])
        report("logs, traces and spend logs", raw, sent)
        self.assertEqual(sum(raw.values()), 0)


@unittest.skipUnless(GATEWAY and MOCK and PRESIDIO_DOWN, "needs MOCK_HOSTED_URL and PRESIDIO_DOWN=1")
class FailClosed(_Keyed):
    def test_a_request_is_refused_when_the_analyzer_is_down(self):
        for stream in (False, True):
            tag = f"pii-fail-closed-{'stream' if stream else 'plain'}"
            status = chat(self.key, HOSTED, f"[{tag}] {PROMPTS[0]['text']}", stream)[0]
            sent = received(tag)
            print(f"\nfail closed ({tag}): status {status}, requests at the provider {len(sent)}")
            self.assertNotIn(status, (200, 401, 403, 404))  # refused by the guardrail, not for another reason
            self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
