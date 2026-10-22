"""The scenario runner's own logic, no cluster: the checks on one answer, the objects a call targets, the
six published metrics, and cluster mode's switch and restore (with a stand-in kubectl). The runner itself
runs on kind (tests/kind-smoke.sh)."""
import hashlib
import json
import os
import pathlib
import sys
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
    """Stands in for rs.kubectl: the gateway container's env and the agent-tokens ConfigMap. With
    restore_sticks=False, every `set env` but the switch to the CLI approver is ignored (a failed restore)."""
    def __init__(self, env, tokens, restore_sticks=True):
        self.env, self.tokens, self.restore_sticks = dict(env), tokens, restore_sticks

    def __call__(self, *args, stdin=None):
        assert list(args[:2]) == ["-n", "action-gateway"], args
        a = list(args[2:])
        if a[:2] == ["get", "deployment"]:
            gateway = {"name": "gateway", "env": [{"name": k, "value": v} for k, v in self.env.items()]}
            return json.dumps({"spec": {"template": {"spec": {"containers": [{"name": "opa"}, gateway]}}}})
        if a[:2] == ["get", "configmap"]:
            return json.dumps({"data": {"tokens.json": self.tokens}})
        if a[:2] == ["patch", "configmap"]:
            self.tokens = json.loads(a[a.index("-p") + 1])["data"]["tokens.json"]
        elif a[:4] == ["set", "env", "deploy/action-gateway", "-c"] and a[4] == "gateway":
            if self.restore_sticks or "APPROVER=cli" in a:
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


class ClusterMode(unittest.TestCase):
    def test_switches_for_the_run_then_restores(self):
        fake = FakeKubectl(ENV, TOKENS)
        with mock.patch.object(rs, "kubectl", fake), rs.cluster_run(RUN):
            self.assertEqual(fake.env, ENV | {"APPROVER": "cli", "TEST_ONLY_CLOCK_FILE": "/data/clock"})
            during = json.loads(fake.tokens)
            self.assertEqual(during, json.loads(TOKENS) | {
                hashlib.sha256(t.encode()).hexdigest(): team for team, t in RUN.items()})
            self.assertNotIn("run-token", fake.tokens)  # hashes only
        self.assertEqual(fake.env, ENV)  # the Slack approver back, the test clock gone
        self.assertEqual(fake.tokens, TOKENS)

    def test_restores_after_a_failed_run(self):
        fake = FakeKubectl(ENV, TOKENS)
        with mock.patch.object(rs, "kubectl", fake), self.assertRaises(ValueError), rs.cluster_run(RUN):
            raise ValueError("a scenario broke")
        self.assertEqual((fake.env, fake.tokens), (ENV, TOKENS))

    def test_keeps_a_clock_that_was_already_set(self):
        env = ENV | {"TEST_ONLY_CLOCK_FILE": "/data/other"}
        fake = FakeKubectl(env, TOKENS)
        with mock.patch.object(rs, "kubectl", fake), rs.cluster_run(RUN):
            self.assertEqual(fake.env["TEST_ONLY_CLOCK_FILE"], "/data/clock")
        self.assertEqual(fake.env, env)

    def test_checks_the_restore(self):
        fake = FakeKubectl(ENV, TOKENS, restore_sticks=False)
        with mock.patch.object(rs, "kubectl", fake), \
                self.assertRaisesRegex(RuntimeError, "putting the gateway back failed"), rs.cluster_run(RUN):
            pass
        self.assertEqual(fake.tokens, TOKENS)  # the other restore still ran

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
