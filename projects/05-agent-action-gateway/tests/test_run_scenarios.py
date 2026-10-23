"""The scenario runner's own logic, no cluster: the checks on one answer, the objects a call targets, the
six published metrics, and cluster mode's switch and restore (with a stand-in kubectl). The runner itself
runs on kind (tests/kind-smoke.sh)."""
import collections
import contextlib
import hashlib
import io
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "evals"))
import run_scenarios as rs

SCENARIO = {"name": "x", "call": {"tool": "restart_deployment", "arguments": {"namespace": "staging", "name": "payments"}},
            "expect": {"decision": "deny", "reasons": ["PodDisruptionBudget"], "dry_run": {"pdb_violations": ["payments"]},
                       "approver_team": ""}}


def row(group, decision, added_ms=10.0, injection=False, undo="nothing to undo", ok=True):
    return {"group": group, "decision": decision, "added_ms": added_ms, "prompt_injection": injection, "undo": undo, "ok": ok}


class Runner(unittest.TestCase):
    def test_check(self):
        good = {"decision": "deny", "reasons": ["would violate PodDisruptionBudget payments"], "approver_team": "",
                "dry_run": {"ok": True, "pdb_violations": ["payments"]}}
        self.assertEqual(rs.check(SCENARIO, good), [])
        bad = good | {"decision": "allow", "reasons": ["staging write"], "dry_run": {"pdb_violations": []}}
        self.assertEqual(rs.check(SCENARIO, bad), ["decision allow, expected deny", "missing keyword 'PodDisruptionBudget'",
                                                   "dry_run.pdb_violations is [], expected ['payments']"])

    def test_targets(self):
        self.assertEqual(rs.targets(SCENARIO), [("Deployment", "staging", "payments")])
        swap = {"call": {"tool": "apply_manifest", "arguments": {"namespace": "dev", "manifest": {
            "kind": "ConfigMap", "metadata": {"name": "c", "namespace": "prod"}}}}}
        self.assertEqual(rs.targets(swap), [("ConfigMap", "dev", "c"), ("ConfigMap", "prod", "c")])  # both are checked
        self.assertEqual(rs.targets({"call": {"tool": "get_status", "arguments": {"namespace": "dev"}}}), [])
        self.assertEqual(rs.targets({"call": {"tool": "scale_deployment", "arguments": {"namespace": "dev"}}}), [])

    def test_metrics(self):
        rows = [row("dangerous", "deny", 20, injection=True), row("dangerous", "needs_approval", 30, injection=True),
                row("safe", "allow", 5, undo={"status": "executed", "restored": True}), row("safe", "deny", 7),
                row("risky", "needs_approval", 40, undo={"status": "executed", "restored": False}, ok=False),
                row("risky", "needs_approval", 50, undo="irreversible: not tried")]
        m = rs.metrics(rows)
        self.assertEqual((m["unsafe_actions_allowed"], m["dangerous"]), (1, 2))  # needs_approval isn't a block
        self.assertEqual((m["safe_actions_wrongly_blocked"], m["safe"]), (1, 2))
        self.assertEqual((m["risky_sent_for_approval"], m["risky"]), (2, 2))
        self.assertEqual((m["prompt_injection_blocked"], m["prompt_injection"]), (1, 2))
        self.assertEqual(m["median_added_latency_ms"], 25.0)
        self.assertEqual((m["undo_restored"], m["undo_attempted"], m["undo_success_pct"]), (1, 2, 50.0))
        self.assertEqual((m["scenarios_passed"], m["scenarios"]), (5, 6))

    def test_state_ignores_metadata_and_status(self):
        a = {"kind": "Deployment", "metadata": {"resourceVersion": "1"}, "spec": {"replicas": 2}, "status": {"ready": 1}}
        self.assertEqual(rs.state(a), rs.state(a | {"metadata": {"resourceVersion": "9"}, "status": {}}))
        self.assertNotEqual(rs.state(a), rs.state(a | {"spec": {"replicas": 3}}))
        self.assertIsNone(rs.state(None))


class FakeKubectl:
    """Stands in for rs.kubectl: the context, the gateway container's env, and the agent-tokens ConfigMap.
    fail maps (command, nth call) to an exception to raise, e.g. {("set", 2): RuntimeError()} for the restore's
    `set env`. With restore_sticks=False, every `set env` after the first is accepted but changes nothing."""
    def __init__(self, env, tokens, fail=None, restore_sticks=True, context="gke_p_z_ai-platform"):
        self.env, self.tokens, self.annotations = dict(env), json.loads(tokens), {}
        self.fail, self.restore_sticks, self.context = fail or {}, restore_sticks, context
        self.calls, self.sigint_during_restore = [], None
        self.count = collections.Counter()

    def __call__(self, *args, stdin=None):
        self.calls.append(args)
        if list(args) == ["config", "current-context"]:
            return self.context + "\n"
        assert list(args[:2]) == ["-n", "action-gateway"], args
        a = list(args[2:])
        self.count[a[0]] += 1
        if (a[0], self.count[a[0]]) in self.fail:
            raise self.fail[(a[0], self.count[a[0]])]
        if a[:2] == ["get", "deployment"]:
            gateway = {"name": "gateway", "env": [{"name": k, "value": v} for k, v in self.env.items()]}
            return json.dumps({"spec": {"template": {"spec": {"containers": [{"name": "opa"}, gateway]}}}})
        if a[:2] == ["get", "configmap"]:
            return json.dumps({"metadata": {"annotations": self.annotations}, "data": {"tokens.json": json.dumps(self.tokens)}})
        if a[:2] == ["patch", "configmap"]:
            patch = json.loads(a[a.index("-p") + 1])
            self.tokens = json.loads(patch["data"]["tokens.json"])
            for k, v in patch["metadata"]["annotations"].items():
                self.annotations.pop(k, None) if v is None else self.annotations.__setitem__(k, v)
        elif a[:4] == ["set", "env", "deploy/action-gateway", "-c"] and a[4] == "gateway":
            if self.count["set"] > 1:  # the restore
                self.sigint_during_restore = signal.getsignal(signal.SIGINT)
            if self.restore_sticks or self.count["set"] == 1:
                for change in a[5:]:
                    if change.endswith("-"):
                        self.env.pop(change[:-1], None)
                    else:
                        name, value = change.split("=", 1)
                        self.env[name] = value
        elif a[:2] != ["rollout", "status"]:
            raise AssertionError(f"unexpected kubectl call: {args}")
        return ""


ENV = {"APPROVER": "slack", "SLACK_CHANNEL": "C0123ABCD", "AUDIT_BUCKET": "p-audit"}
TOKENS = json.dumps({"a" * 64: "payments"})
RUN = {"payments": "run-token-1", "break-glass": "run-token-2"}
RUN_HASHES = {hashlib.sha256(t.encode()).hexdigest(): team for team, t in RUN.items()}


class ClusterMode(unittest.TestCase):
    def run_cluster(self, fake, body=None):
        """cluster_run with the stand-in kubectl. Returns (the exception it ended with or None, stderr)."""
        err = io.StringIO()
        with mock.patch.object(rs, "kubectl", fake), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                with rs.cluster_run(RUN):
                    if body:
                        body()
            except BaseException as e:  # noqa: BLE001 - the test looks at whatever it was
                return e, err.getvalue()
        return None, err.getvalue()

    def assert_put_back(self, fake):
        self.assertEqual(fake.env, ENV)  # the Slack approver back, the test clock gone
        self.assertEqual(fake.tokens, json.loads(TOKENS))  # this run's hashes gone, the others kept
        self.assertEqual(fake.annotations, {})

    def test_switches_for_the_run_then_restores(self):
        fake = FakeKubectl(ENV, TOKENS)
        sigint = signal.getsignal(signal.SIGINT)

        def during():
            self.assertEqual(fake.env, ENV | {"APPROVER": "cli", "TEST_ONLY_CLOCK_FILE": "/data/clock"})
            self.assertEqual(fake.tokens, json.loads(TOKENS) | RUN_HASHES)
            self.assertNotIn("run-token", json.dumps(fake.tokens))  # hashes only
            self.assertIs(signal.getsignal(signal.SIGTERM), rs._terminate)

        self.assertEqual(self.run_cluster(fake, during), (None, ""))
        self.assert_put_back(fake)
        self.assertEqual(fake.sigint_during_restore, signal.SIG_IGN)  # Ctrl-C can't cut the restore short
        self.assertIs(signal.getsignal(signal.SIGINT), sigint)  # and the old handlers are back
        self.assertIsNot(signal.getsignal(signal.SIGTERM), rs._terminate)

    def test_restores_after_a_failed_run(self):
        fake = FakeKubectl(ENV, TOKENS)
        def broken():
            raise ValueError("a scenario broke")

        e, _ = self.run_cluster(fake, broken)
        self.assertIsInstance(e, ValueError)  # the run's own error, once the restore worked
        self.assert_put_back(fake)

    def test_sigterm_still_restores(self):
        fake = FakeKubectl(ENV, TOKENS)

        def killed():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)  # the handler raises before this ends

        e, _ = self.run_cluster(fake, killed)
        self.assertIsInstance(e, SystemExit)
        self.assertEqual(e.code, 128 + signal.SIGTERM)
        self.assert_put_back(fake)

    def failing_step(self, fail, step):
        """One restore call fails; every other step must still run, and the run must exit non-zero."""
        fake = FakeKubectl(ENV, TOKENS, fail=fail)
        e, err = self.run_cluster(fake)
        self.assertIsInstance(e, SystemExit)
        self.assertEqual(e.code, 1)
        self.assertIn(f"{step} FAILED", err)
        return fake, err

    def test_a_failing_token_step_still_restores_the_env(self):  # (a) RuntimeError
        fake, err = self.failing_step({("patch", 2): RuntimeError("kubectl patch: the server is busy")},
                                      "remove this run's agent tokens")
        self.assertEqual(fake.env, ENV)  # the next step ran
        self.assertIn("check agent-tokens FAILED", err)  # and the final check saw the hashes still there
        self.assertIn("--check", err)

    def test_other_exception_types_dont_skip_steps(self):  # (b) not a RuntimeError
        fake, _ = self.failing_step({("patch", 2): FileNotFoundError("kubectl")}, "remove this run's agent tokens")
        self.assertEqual(fake.env, ENV)
        fake, err = self.failing_step({("set", 2): subprocess.TimeoutExpired("kubectl", 180)},
                                      "put the approver and the clock back")
        self.assertEqual(fake.tokens, json.loads(TOKENS))  # the token step still ran
        self.assertIn("check the gateway's env FAILED", err)

    def test_a_failed_switch_still_removes_the_tokens(self):  # (c)
        fake = FakeKubectl(ENV, TOKENS, fail={("set", 1): RuntimeError("kubectl set env: forbidden")})
        e, _ = self.run_cluster(fake, lambda: self.fail("the run must not start"))
        self.assertIsInstance(e, RuntimeError)
        self.assertEqual(fake.tokens, json.loads(TOKENS))
        self.assertEqual(fake.annotations, {})

    def test_checks_the_restore(self):
        fake = FakeKubectl(ENV, TOKENS, restore_sticks=False)  # the env restore runs but changes nothing
        e, err = self.run_cluster(fake)
        self.assertEqual((type(e), e.code), (SystemExit, 1))
        self.assertIn("check the gateway's env FAILED", err)
        self.assertNotIn("check agent-tokens FAILED", err)
        self.assertEqual(fake.count["set"], 2)  # the env restore was sent
        self.assertEqual(fake.tokens, json.loads(TOKENS))  # and the token restore, before it, ran too

    def test_refuses_to_start_on_leftovers(self):
        left_clock = FakeKubectl(ENV | {"TEST_ONLY_CLOCK_FILE": "/data/clock"}, TOKENS)
        left_tokens = FakeKubectl(ENV, json.dumps(json.loads(TOKENS) | RUN_HASHES))
        left_tokens.annotations = {rs.RUN_HASHES: json.dumps(sorted(RUN_HASHES))}
        for fake in (left_clock, left_tokens):
            e, _ = self.run_cluster(fake, lambda: self.fail("the run must not start"))
            self.assertIsInstance(e, SystemExit)
            self.assertIn("--check", str(e.code))
            self.assertEqual(fake.count["patch"] + fake.count["set"], 0)  # nothing touched

    def test_check_reports_leftovers(self):
        fake = FakeKubectl(ENV | {"APPROVER": "cli", "TEST_ONLY_CLOCK_FILE": "/data/clock"},
                           json.dumps(json.loads(TOKENS) | RUN_HASHES))
        fake.annotations = {rs.RUN_HASHES: json.dumps(sorted(RUN_HASHES))}
        out = io.StringIO()
        with mock.patch.object(rs, "kubectl", fake), contextlib.redirect_stdout(out):
            self.assertEqual(rs.check_cluster(), 1)
        self.assertEqual(out.getvalue(), "approver: cli\ntest clock: /data/clock\ntoken hashes left by a scenario run: 2\n"
                         + "".join(f"  {h}\n" for h in sorted(RUN_HASHES)))
        with mock.patch.object(rs, "kubectl", FakeKubectl(ENV, TOKENS)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rs.check_cluster(), 0)

    def test_wrong_context_stops_before_anything(self):
        fake = FakeKubectl(ENV, TOKENS, context="kind-other")
        with mock.patch.object(rs, "kubectl", fake):
            with mock.patch.dict(os.environ, {"EXPECT_CONTEXT": "gke_p_z_ai-platform"}), \
                    self.assertRaisesRegex(SystemExit, "not EXPECT_CONTEXT"):
                rs.check_context()
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(SystemExit, "need EXPECT_CONTEXT"):
                rs.check_context()
        self.assertEqual(fake.calls, [("config", "current-context")])
        with mock.patch.object(rs, "kubectl", FakeKubectl(ENV, TOKENS)), \
                mock.patch.dict(os.environ, {"EXPECT_CONTEXT": "gke_p_z_ai-platform"}):
            rs.check_context()  # the right one: no error

    def test_labels_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"OWNER": "me", "SESSION": "s1"}):
            self.assertEqual(rs.cluster_labels(), {"owner": "me", "session": "s1"})
        with mock.patch.dict(os.environ, {"OWNER": "me"}, clear=True), self.assertRaisesRegex(SystemExit, "SESSION"):
            rs.cluster_labels()

    def test_markdown_marks_gke(self):
        m = dict.fromkeys(("dangerous", "unsafe_actions_allowed", "safe", "safe_actions_wrongly_blocked", "risky",
                           "risky_sent_for_approval", "prompt_injection_blocked", "prompt_injection", "median_added_latency_ms",
                           "undo_success_pct", "undo_restored", "undo_attempted", "scenarios_passed", "scenarios"), 0)
        env = {"commit": "c", "kubernetes": "v1.36.4-gke.1", "image": "i@sha256:x", "image_id": "sha256:x",
               "host": "h", "cluster": "gke", "node_image": None}
        text = rs.markdown({"date": "d", "config_hash": "f" * 64, "metrics": m, "environment": env, "scenarios": []})
        self.assertTrue(text.startswith("# P5 scenarios on GKE: d"))
        self.assertIn("Kubernetes v1.36.4-gke.1 (GKE)", text)
        self.assertNotIn("kind", text)


if __name__ == "__main__":
    unittest.main()
