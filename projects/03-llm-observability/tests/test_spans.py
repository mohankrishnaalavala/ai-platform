"""The span contract: the span names and attributes LiteLLM (v1.103.3) sends for each kind of request.
P3 turns these spans into metrics, so a LiteLLM upgrade that renames one breaks a dashboard, an alert or
the scale-from-zero trigger. This test catches that in CI.

What LiteLLM sends for one request to /v1/chat/completions:
- One SERVER span, "Received Proxy Server Request", with http.route, http.response.status_code and the
  team (metadata.user_api_key_team_alias). It has no model: the availability SLI counts it by status.
- One "litellm_request" span per attempt at a model, in the same trace. Each has litellm.model_group
  (chat-default or chat-hosted), the team, and status ERROR if the attempt failed. A successful attempt
  also has its cost (gen_ai.cost.total_cost), at the price the spend logs use.
- No "fell back" flag. A fallback is a failed chat-default attempt, then a successful chat-hosted one,
  in one trace. So P3 counts fallbacks as failed chat-default attempts.
- A request refused for its budget gets a 429 SERVER span: error codes land on the same attribute.

Runs in ci.yml's gateway job, between P2's tests and P2's fallback step. It stops the mock vLLM itself
(docker compose, from P2's tests folder) and leaves it stopped, as P2's fallback step wants. Restarting a
mock between requests can leave LiteLLM sending to the old container's address, so it doesn't. Then it
reads what the collector wrote to SPANS_FILE (compose.p3.yaml).
Its fallback request is the first one after the stop, the one that waits out chat-default's timeout. So it
also checks what P2's fallback test checked before this ran first: the answer comes within the timeout.
Skipped unless GATEWAY_URL, MASTER_KEY and SPANS_FILE are set.
"""
import json
import os
import subprocess
import sys
import time
import unittest

P2_TESTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "02-llm-gateway", "tests")
sys.path.insert(0, P2_TESTS)
from client import MASTER, URL, chat, onboard_team
from test_fallback import SLACK_S, TIMEOUT_S

SPANS_FILE = os.environ.get("SPANS_FILE", "")
SERVER, ATTEMPT = "Received Proxy Server Request", "litellm_request"
ROUTE = "/v1/chat/completions"
SPAN_KIND_SERVER, STATUS_ERROR = 2, 2  # OTLP's enum values, as the collector's file exporter writes them
# chat-default's price is $0.00000075 per output token (helm/gateway/values.yaml; CI checks that
# values-ci.yaml matches it), and the mock always reports 10 output tokens.
COST_PER_CALL = 10 * 0.00000075


def value(v):
    """An OTLP JSON attribute value: intValue arrives as a string."""
    kind, raw = next(iter(v.items()), (None, None))
    return int(raw) if kind == "intValue" else raw


def read_spans():
    spans = []
    with open(SPANS_FILE) as f:
        for line in f:
            for rs in json.loads(line).get("resourceSpans", []):
                for ss in rs.get("scopeSpans", []):
                    for s in ss.get("spans", []):
                        spans.append({
                            "name": s["name"], "trace": s["traceId"], "kind": s.get("kind"),
                            "error": s.get("status", {}).get("code") == STATUS_ERROR,
                            "start": int(s.get("startTimeUnixNano", 0)),
                            "attrs": {a["key"]: value(a["value"]) for a in s.get("attributes", [])}})
    return spans


@unittest.skipUnless(URL and MASTER and SPANS_FILE, "needs the CI gateway stack (GATEWAY_URL, MASTER_KEY, SPANS_FILE)")
class SpanContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start, stamp = time.monotonic(), int(time.time() * 1000) % 10**8
        # One team per path, so each path's spans are found by team alone. "budget" can't afford one call.
        budgets = {"plain": 1, "stream": 1, "budget": 0.000001, "fallback": 1}
        cls.teams = {p: f"spans-{p}-{stamp}" for p in budgets}
        keys = {p: onboard_team.create_key(URL, MASTER, onboard_team.create_team(URL, MASTER, t, budgets[p], 100), f"{t}-1")
                for p, t in cls.teams.items()}

        def ask(path, **kw):
            return chat(keys[path], "chat-default", "Reply with the single word: ok", **kw)[0]

        cls.statuses = {"plain": [ask("plain")], "stream": [ask("stream", stream=True)], "budget": []}
        for _ in range(15):  # spend is written in batches, so the block comes a few calls late
            cls.statuses["budget"].append(ask("budget"))
            if cls.statuses["budget"][-1] == 429:
                break
            time.sleep(1)
        subprocess.run(["docker", "compose", "stop", "mock-vllm"], cwd=P2_TESTS, check=True, capture_output=True)
        before = time.monotonic()
        cls.statuses["fallback"] = [ask("fallback")]
        cls.fallback_s = time.monotonic() - before
        print(f"\nstatus codes per path: {cls.statuses}, sent in {time.monotonic() - start:.0f} s; "
              f"the first request after the stop fell back in {cls.fallback_s * 1000:.0f} ms")
        cls.spans = {}
        attempts = {"plain": 1, "stream": 1, "fallback": 2}  # attempt spans can come in a later batch
        for _ in range(30):  # LiteLLM sends spans in batches
            time.sleep(2)
            try:
                spans = read_spans()
            except (FileNotFoundError, ValueError):  # nothing exported yet, or a line half written
                continue
            cls.spans = {p: [s for s in spans if s["attrs"].get("metadata.user_api_key_team_alias") == t]
                         for p, t in cls.teams.items()}
            if all(len(cls.server(p)) == len(cls.statuses[p]) for p in cls.teams) and \
                    all(sum(s["name"] == ATTEMPT for s in cls.spans[p]) >= n for p, n in attempts.items()):
                break
        print(f"spans per path: { {p: len(v) for p, v in cls.spans.items()} }, read after {time.monotonic() - start:.0f} s")

    @classmethod
    def server(cls, path):
        return sorted((s for s in cls.spans[path] if s["name"] == SERVER), key=lambda s: s["start"])

    def attempts(self, path):
        """The path's one SERVER span, and its attempt spans in order."""
        servers = self.server(path)
        self.assertEqual(len(servers), 1, f"{path}: server spans")
        tried = sorted((s for s in self.spans[path] if s["name"] == ATTEMPT and s["trace"] == servers[0]["trace"]),
                       key=lambda s: s["start"])
        return servers[0], tried

    def check_server(self, span, status):
        self.assertEqual(span["kind"], SPAN_KIND_SERVER)
        self.assertEqual(span["attrs"].get("http.route"), ROUTE)
        self.assertEqual(span["attrs"].get("http.response.status_code"), status)

    def check_answered_by_vllm(self, path):
        server, tried = self.attempts(path)
        self.check_server(server, 200)
        self.assertEqual([(a["attrs"].get("litellm.model_group"), a["error"]) for a in tried], [("chat-default", False)])
        self.assertAlmostEqual(tried[0]["attrs"].get("gen_ai.cost.total_cost"), COST_PER_CALL)

    def test_plain(self):
        self.check_answered_by_vllm("plain")

    def test_stream(self):
        self.check_answered_by_vllm("stream")

    def test_budget_refusal_is_a_429_server_span(self):
        self.assertEqual(self.statuses["budget"][-1], 429)
        self.assertEqual([s["attrs"].get("http.response.status_code") for s in self.server("budget")], self.statuses["budget"])
        self.check_server(self.server("budget")[-1], 429)

    def test_fallback_is_a_failed_then_a_successful_attempt(self):
        server, tried = self.attempts("fallback")
        self.check_server(server, 200)
        self.assertEqual([(a["attrs"].get("litellm.model_group"), a["error"]) for a in tried],
                         [("chat-default", True), ("chat-hosted", False)])
        self.assertIn("gen_ai.cost.total_cost", tried[1]["attrs"])  # CI's mock hosted model has no price
        self.assertLess(self.fallback_s, TIMEOUT_S + SLACK_S)


if __name__ == "__main__":
    unittest.main()
