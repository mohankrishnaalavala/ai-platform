"""The canary contract: what promote.py relies on in LiteLLM v1.103.3, proven in CI's gateway stack (P2's
compose stack with P4's values, mock providers) before any cloud run. It uses promote.py's own calls.

1. Traffic split: chat-hosted's two canary deployments, stored in the gateway's database at weights 90:10,
   get about 90% and 10% of the alias's requests. What reached the mock provider agrees. The alias's
   deployment from the config file has no weight, so it gets none.
2. Weights change at runtime with no restart: PATCH /model/{id}/update moves every request to one side at
   once (100%, then a rollback to 0%), and the gateway container's start time and restart count stay put.
3. litellm.provider.model (the deployment's provider/model) is on every attempt span, ok and failed, and
   reaches Prometheus as its own label (P3's span metrics), so promote.py can count each deployment's errors.
4. Which deployment answered: the x-litellm-model-id header (the deployment's id), on plain and streamed
   answers; x-litellm-model-name has its provider/model. The body's "model" is only the alias. A request
   may name a deployment's id as its model to reach just that one, at any weight: promote.py's probe and
   live eval use that.
Also: the judge alias reaches its provider at temperature 0, and promote.py's key reaches the canary's two
deployment ids and the alias but no other model.

Runs in ci.yml's gateway job after P2's and P3's checks, and deletes its two deployments at the end.
Skipped unless GATEWAY_URL, MASTER_KEY, MOCK_HOSTED_URL, SPANS_FILE and PROMETHEUS_URL are set.
"""
import collections
import json
import math
import os
import subprocess
import sys
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
P2_TESTS = os.path.join(HERE, "..", "..", "02-llm-gateway", "tests")
sys.path[:0] = [os.path.join(HERE, "..", "scripts"), P2_TESTS, os.path.join(HERE, "..", "..", "03-llm-observability", "tests")]
import promote
from client import MASTER, URL, chat
from test_metrics import PROMETHEUS, until
from test_spans import SPANS_FILE, read_spans

MOCK = os.environ.get("MOCK_HOSTED_URL", "")
BASE, CAND = promote.BASELINE_ID, promote.CANDIDATE_ID
# CI's stand-ins for gemini-3.5-flash-lite and gemini-3.5-flash, both answered by the mock hosted provider.
MODELS = {BASE: "openai/flash-lite", CAND: "openai/flash"}
MOCK_PARAMS = {"api_base": "http://mock-hosted:8000/v1", "api_key": "unused"}
N, SHARE = 300, 0.10


def mock_log():
    with urllib.request.urlopen(MOCK + "/log", timeout=10) as r:
        return [json.loads(line) for line in r.read().decode().splitlines()]


def container():
    """The gateway container's start time and restart count."""
    cid = subprocess.run(["docker", "compose", "ps", "-q", "litellm"], cwd=P2_TESTS, check=True,
                         capture_output=True, text=True).stdout.strip()
    return subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}} {{.RestartCount}}", cid],
                          check=True, capture_output=True, text=True).stdout.strip()


def answered_by(n, model="chat-hosted"):
    return collections.Counter(chat(MASTER, model, "Reply with the single word: ok")[2].get("x-litellm-model-id")
                               for _ in range(n))


@unittest.skipUnless(URL and MASTER and MOCK and SPANS_FILE and PROMETHEUS,
                     "needs the CI gateway stack (GATEWAY_URL, MASTER_KEY, MOCK_HOSTED_URL, SPANS_FILE, PROMETHEUS_URL)")
class CanaryContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start = container()
        promote.add_deployments(MODELS[BASE], MODELS[CAND], MOCK_PARAMS)
        promote.set_weights(10, 0)
        seen = len(mock_log())
        cls.split = answered_by(N)
        cls.mock_split = collections.Counter(b["model"] for b in mock_log()[seen:])
        cls.plain = chat(MASTER, "chat-hosted", "Reply with the single word: ok")
        cls.stream = chat(MASTER, "chat-hosted", "Reply with the single word: ok", stream=True)
        promote.set_weights(100, 10)
        cls.at_100 = answered_by(30)
        promote.set_weights(0, 100)
        cls.rolled_back = answered_by(30)
        cls.by_id = {mid: answered_by(3, mid) for mid in MODELS}  # the candidate is at weight 0 now
        cls.key = promote.new_key(1)
        cls.key_calls = {m: chat(cls.key, m, "Reply with the single word: ok")[0] for m in (*MODELS, "chat-hosted", "judge")}
        cls.end = container()
        # A failing candidate: nothing listens on port 9.
        cls.errors_before = promote.attempts(MODELS[CAND]).get(promote.ERROR, 0)
        promote.api("PATCH", f"/model/{CAND}/update", {"litellm_params": {"api_base": "http://mock-hosted:9/v1"}})
        cls.failed = [chat(MASTER, CAND, "Reply with the single word: ok")[0] for _ in range(3)]
        seen = len(mock_log())
        cls.judge = chat(MASTER, "judge", "Reply with the single word: ok")[0], mock_log()[seen:]

    @classmethod
    def tearDownClass(cls):
        promote.api("POST", "/key/delete", {"keys": [cls.key]})
        for mid in MODELS:
            promote.api("POST", "/model/delete", {"id": mid})

    def test_1_traffic_split_follows_the_weights(self):
        print(f"\n{N} requests at 90:10, answered by: {dict(self.split)}; the mock got: {dict(self.mock_split)}")
        sigma = math.sqrt(N * SHARE * (1 - SHARE))
        self.assertLess(abs(self.split[CAND] - N * SHARE), 4.5 * sigma)  # a false failure about 1 in 100,000 runs
        self.assertEqual(sum(self.split.values()), self.split[BASE] + self.split[CAND], "the config deployment got traffic")
        self.assertEqual(dict(self.mock_split), {"flash-lite": self.split[BASE], "flash": self.split[CAND]})

    def test_2_a_weight_change_needs_no_restart(self):
        print(f"\nat 100%: {dict(self.at_100)}; after the rollback: {dict(self.rolled_back)}; container: {self.start} -> {self.end}")
        self.assertEqual(self.at_100, {CAND: 30})
        self.assertEqual(self.rolled_back, {BASE: 30})
        self.assertEqual(self.start, self.end)

    def test_3_provider_model_is_on_attempt_spans_and_in_prometheus(self):
        self.assertEqual(self.failed, [500, 500, 500])

        def spans():
            found = collections.Counter((s["attrs"].get("litellm.model_group"), s["attrs"].get("litellm.provider.model"), s["error"])
                                        for s in read_spans() if s["name"] == "litellm_request")
            return found if found[(CAND, MODELS[CAND], True)] >= 3 else None
        found = until(spans)
        print(f"\nattempt spans (model group, provider model, failed): { {k: v for k, v in (found or {}).items() if k[1] in MODELS.values()} }")
        self.assertTrue(found, "no failed attempt span with the candidate's provider model")
        for mid, model in MODELS.items():
            self.assertGreaterEqual(found[("chat-hosted", model, False)], self.split[mid] + self.at_100[mid] + self.rolled_back[mid])
            self.assertGreaterEqual(found[(mid, model, False)], 3)  # the calls that named the id
        errors = until(lambda: promote.attempts(MODELS[CAND]).get(promote.ERROR, 0) - self.errors_before >= 3 or None)
        self.assertTrue(errors, "Prometheus has no failed attempts for the candidate's provider model")

    def test_4_a_header_names_the_deployment(self):
        status, body, headers, _ = self.plain
        self.assertEqual(status, 200)
        self.assertEqual(body["model"], "chat-hosted")  # the alias, whichever deployment answered
        self.assertIn(headers.get("x-litellm-model-id"), MODELS)
        self.assertEqual(headers.get("x-litellm-model-name"), MODELS[headers["x-litellm-model-id"]])
        self.assertEqual(self.stream[0], 200)
        self.assertIn(self.stream[2].get("x-litellm-model-id"), MODELS)
        self.assertEqual(self.by_id, {BASE: {BASE: 3}, CAND: {CAND: 3}})

    def test_promotes_key_reaches_the_canary_only(self):
        print(f"\nstatus per model with promote.py's key: {self.key_calls}")
        self.assertEqual({m: self.key_calls[m] for m in (*MODELS, "chat-hosted")}, {BASE: 200, CAND: 200, "chat-hosted": 200})
        self.assertIn(self.key_calls["judge"], (401, 403))

    def test_judge_reaches_its_provider_at_temperature_0(self):
        status, bodies = self.judge
        self.assertEqual(status, 200)
        self.assertEqual([(b["model"], b.get("temperature")) for b in bodies], [("judge", 0)])


if __name__ == "__main__":
    unittest.main()
