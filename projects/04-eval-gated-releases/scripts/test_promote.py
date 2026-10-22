"""Unit tests for promote.py and probe.py against a fake gateway, a fake Prometheus, and fake kubectl and gh.
Standard library only (CI's unit-test step installs nothing)."""
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import probe
import promote

BASE, CAND = promote.BASELINE_ID, promote.CANDIDATE_ID
CASES = [  # the golden set's format; the judge case is never picked
    {"id": "instruction-001", "category": "instruction", "prompt": "p1", "check": {"type": "max_words", "value": 3}},
    {"id": "pii-001", "category": "safety", "prompt": "p2", "system": "s", "check": {"type": "must_not_contain", "values": ["Jane"]}},
    {"id": "qa-001", "category": "qa", "prompt": "p3", "check": {"type": "judge"}},
    {"id": "structured-001", "category": "structured", "prompt": "p4", "check": {"type": "json_schema", "schema": {}}},
]
GOOD, BAD = '{"ok": true}', "Jane wrote no JSON here at all"


class Fake(BaseHTTPRequestHandler):
    """The gateway's admin and chat API, and Prometheus's query API, as promote.py and probe.py use them.
    Each test sets Fake.state first."""

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # Prometheus: the next scripted answer
        counts = self.state["prometheus"].pop(0)
        self.reply(200, {"data": {"result": [{"metric": {"status_code": k}, "value": [0, str(v)]} for k, v in counts.items()]}})

    def do_PATCH(self):
        mid = self.path.split("/")[2]
        if mid not in self.state["weights"]:
            return self.reply(404, {})
        self.state["weights"][mid] = self.body()["litellm_params"]["weight"]
        self.state["patches"].append((mid, self.state["weights"][mid]))
        self.reply(200, {})

    def do_POST(self):
        body, s = self.body(), self.state
        if self.path == "/model/new":
            s["weights"][body["model_info"]["id"]] = body["litellm_params"]["weight"]
        elif self.path == "/model/delete":
            if s["weights"].pop(body["id"], None) is None:
                return self.reply(404, {})
        elif self.path == "/key/generate":
            return self.reply(200, {"key": "sk-test"})
        elif self.path == "/key/delete":
            s["deleted_keys"] += body["keys"]
        elif self.path == "/v1/chat/completions" and body.get("stream"):
            events = [{"choices": [{"delta": {"role": "assistant", "content": ""}}]}, {"choices": [{"delta": {"content": "ok"}}]}]
            return self.reply(200, "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode() + b"data: [DONE]\n\n", "text/event-stream")
        elif self.path == "/v1/chat/completions":
            s["chats"].append(body)
            return self.reply(200, {"choices": [{"message": {"content": s["answers"][body["model"]]}}]})
        self.reply(200, {})

    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def log_message(self, *args):
        pass


def probe_result(base_s=1.0, cand_s=1.1):
    return json.dumps({BASE: {"n": 20, "errors": 0, "ttft_p95_s": base_s}, CAND: {"n": 20, "errors": 0, "ttft_p95_s": cand_s}})


class PromoteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(c) for c in CASES) + "\n")
        cls.cases = f.name

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        os.unlink(cls.cases)

    def run_canary(self, prometheus, job="1/", probe_log=None, candidate_answer=GOOD, extra=()):
        Fake.state = {"weights": {}, "patches": [], "chats": [], "deleted_keys": [], "prometheus": prometheus,
                      "answers": {BASE: GOOD, CAND: candidate_answer}}
        self.kubectl, self.issues = [], []

        def sh(*cmd, stdin=None):
            if cmd[0] == "gh":
                self.issues.append((cmd, stdin))
                return ""
            self.kubectl.append((cmd, stdin))
            if cmd[1:3] == ("get", "namespace"):
                return json.dumps({"metadata": {"labels": {"owner": "o", "session": "s"}}})
            if cmd[1:3] == ("get", "job"):
                return job
            if cmd[1] == "logs":
                return "some line\n" + (probe_log or probe_result())
            return ""

        env = {"GATEWAY_URL": self.url, "PROMETHEUS_URL": self.url, "MASTER_KEY": "sk-master"}
        with mock.patch.dict(os.environ, env), mock.patch.object(promote, "sh", sh), redirect_stdout(io.StringIO()) as out:
            code = promote.main(["--cases", self.cases, "--live-cases", "3", "--settle", "0", *extra])
        self.out = out.getvalue()
        return code

    def assert_rolled_back(self, code, pct, reason):
        self.assertEqual(code, 1)
        self.assertEqual(Fake.state["weights"], {BASE: 100, CAND: 0})
        self.assertEqual(len(self.issues), 1)
        cmd, body = self.issues[0]
        self.assertIn(f"at {pct}%", cmd[cmd.index("--title") + 1])
        self.assertIn(reason, body)
        self.assertNotIn(BAD, self.out + body)  # answers are never printed
        self.assertEqual(Fake.state["deleted_keys"], ["sk-test"])

    def test_promotes_when_every_check_passes(self):
        code = self.run_canary([{}, {"STATUS_CODE_OK": 40}, {"STATUS_CODE_OK": 80}, {"STATUS_CODE_OK": 120}])
        self.assertEqual(code, 0, self.out)
        self.assertEqual(Fake.state["weights"], {BASE: 0, CAND: 100})
        # Each step raises the candidate first, so the two are never 0 at once.
        self.assertEqual(Fake.state["patches"], [(CAND, 10), (BASE, 90), (CAND, 50), (BASE, 50), (CAND, 100), (BASE, 0)])
        self.assertEqual(self.issues, [])
        self.assertEqual(Fake.state["deleted_keys"], ["sk-test"])
        # The live eval: 3 deterministic cases (no judge), each to both deployments by id, at temperature 0.
        self.assertEqual(sorted({(c["model"], c["temperature"]) for c in Fake.state["chats"]}), [(BASE, 0), (CAND, 0)])
        self.assertEqual(len(Fake.state["chats"]), 3 * 2 * 3)
        self.assertEqual(Fake.state["chats"][2]["messages"][0], {"role": "system", "content": "s"})
        # The probe Job: its key in a Secret (stdin), never in a command's arguments.
        applied = [json.loads(stdin) for cmd, stdin in self.kubectl if cmd[1] == "apply"]
        self.assertEqual(len(applied), 3)
        self.assertEqual(applied[0]["items"][1]["stringData"], {"GATEWAY_KEY": "sk-test"})
        self.assertFalse(any("sk-test" in " ".join(cmd) for cmd, _ in self.kubectl))

    def test_errors_roll_back_and_open_an_issue(self):
        code = self.run_canary([{}, {"STATUS_CODE_OK": 38, "STATUS_CODE_ERROR": 2}])
        self.assert_rolled_back(code, 10, "2 of 40 candidate attempts failed")
        self.assertEqual(sum(cmd[1] == "apply" for cmd, _ in self.kubectl), 1)  # no further step

    def test_no_candidate_data_fails_closed(self):
        self.assert_rolled_back(self.run_canary([{}, {}]), 10, "no candidate attempts in Prometheus")

    def test_a_slow_candidate_rolls_back(self):
        code = self.run_canary([{}, {"STATUS_CODE_OK": 40}], probe_log=probe_result(1.0, 1.3))
        self.assert_rolled_back(code, 10, "TTFT p95: candidate 1.30 s, over baseline 1.00 s x 1.2")

    def test_a_worse_live_eval_rolls_back(self):
        code = self.run_canary([{}, {"STATUS_CODE_OK": 40}], candidate_answer=BAD)
        self.assert_rolled_back(code, 10, "live eval, safety: candidate passed 0, baseline 1")

    def test_a_failed_probe_job_rolls_back(self):
        self.assert_rolled_back(self.run_canary([{}], job="/1"), 10, "the probe Job failed")

    def test_rollback_only(self):
        Fake.state = {"weights": {BASE: 0, CAND: 100}, "patches": []}
        with mock.patch.dict(os.environ, {"GATEWAY_URL": self.url, "MASTER_KEY": "k"}), redirect_stdout(io.StringIO()):
            self.assertEqual(promote.main(["--rollback"]), 0)
            self.assertEqual(Fake.state["patches"], [(BASE, 100), (CAND, 0)])
            Fake.state["weights"] = {}
            self.assertEqual(promote.main(["--rollback"]), 0)  # nothing there: nothing to do

    def test_a_bad_model_name_is_refused(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            promote.parse(["--candidate", 'x"} or vector(1)'])

    def test_probe_measures_each_deployment(self):
        Fake.state = {}
        env = {"GATEWAY_URL": self.url, "GATEWAY_KEY": "k", "TARGETS": f"{BASE} {CAND}", "REQUESTS": "3"}
        with mock.patch.dict(os.environ, env), redirect_stdout(io.StringIO()) as out:
            probe.main()
        result = json.loads(out.getvalue())
        self.assertEqual({k: (v["n"], v["errors"]) for k, v in result.items()}, {BASE: (3, 0), CAND: (3, 0)})
        self.assertGreater(result[CAND]["ttft_p95_s"], 0)

    def test_p95_is_the_nearest_rank(self):
        self.assertEqual(probe.p95(list(range(1, 21))), 19)
        self.assertEqual(probe.p95([5]), 5)
        self.assertIsNone(probe.p95([]))


if __name__ == "__main__":
    unittest.main()
