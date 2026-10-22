"""The action pipeline against an in-memory API server: facts, decisions, approval, audit, undo."""
import copy
import datetime
import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import actions
import audit
from kube import KubeError

T = datetime.datetime(2026, 10, 21, 15, 30, tzinfo=datetime.timezone.utc)


def deployment(ns, name, replicas=2, revision=1):
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": ns, "uid": f"uid-{name}", "generation": 1, "resourceVersion": "1",
                         "annotations": {"deployment.kubernetes.io/revision": str(revision)}},
            "spec": {"replicas": replicas, "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": {"app": name}},
                                  "spec": {"containers": [{"name": "app", "image": "app:v2"}]}}},
            "status": {"observedGeneration": 1, "readyReplicas": replicas, "availableReplicas": replicas}}


def merge(a, b):  # JSON merge patch (RFC 7386)
    if not isinstance(b, dict):
        return b
    out = dict(a) if isinstance(a, dict) else {}
    for k, v in b.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge(out.get(k), v)
    return out


class FakeKube:
    """Enough of the API server for the pipeline: get, list, patch (merge, JSON, apply), delete, dry run."""
    def __init__(self, objects=(), envs=None, pdbs=(), replicasets=()):
        self.objects = {(o["kind"], o["metadata"]["namespace"], o["metadata"]["name"]): o for o in objects}
        self.envs = envs if envs is not None else {"dev": "dev", "staging": "staging", "prod": "prod"}
        self.pdbs, self.replicasets, self.sent = list(pdbs), list(replicasets), []

    def get(self, kind, ns, name):
        return copy.deepcopy(self.objects.get((kind, ns, name)))

    def namespace_env(self, ns):
        return self.envs.get(ns)

    def items(self, path):
        if "poddisruptionbudgets" in path:
            return copy.deepcopy(self.pdbs)
        if "replicasets" in path:
            return copy.deepcopy(self.replicasets)
        return [copy.deepcopy(o) for (k, ns, _), o in self.objects.items() if k == "Deployment" and f"/{ns}/" in path]

    def send(self, kind, ns, name, change, dry_run):
        self.sent.append((change["type"], dry_run))
        key, old = (kind, ns, name), self.get(kind, ns, name)
        if change["type"] == "apply" and change["body"]["metadata"]["namespace"] != ns:
            raise KubeError(400, "the namespace of the provided object does not match the namespace sent on the request")
        if old is None and change["type"] != "apply":
            raise KubeError(404, "not found")
        if change["type"] == "delete":
            if not dry_run:
                del self.objects[key]
            return None
        if change["type"] == "json":
            new = copy.deepcopy(old)
            for op in change["body"]:
                *parents, last = op["path"].strip("/").split("/")
                target = new
                for p in parents:
                    target = target[p]
                target[last] = op["value"]
        else:
            new = merge(old or {}, change["body"])
        meta = new.setdefault("metadata", {})
        meta.setdefault("uid", f"uid-{name}")
        meta["resourceVersion"] = str(int(meta.get("resourceVersion", "0")) + 1)
        if old is None or old.get("spec") != new.get("spec"):
            meta["generation"] = meta.get("generation", 0) + 1
        if not dry_run:
            self.objects[key] = new
        return copy.deepcopy(new)


class Approver:
    def __init__(self, approved=True):
        self.approved, self.requests = approved, []

    def decide(self, action_id, request, timeout_s):
        self.requests.append(request)
        return {"approved": self.approved, "approver": "alice" if self.approved else None, "via": "test"}


class MemoryAudit:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(json.loads(json.dumps(record)))

    def read(self, action_id):
        return next((r for r in self.records if r["action_id"] == action_id), None)


def simple_policy(facts):
    """Stands in for the Rego: unknown arguments, no env label or a failed dry run deny; prod writes need approval."""
    _, props, _ = actions.TOOLS[facts["tool"]]
    if set(facts["args"]) - set(props) or "env" not in facts["namespace"] or not (facts.get("dry_run") or {"ok": True})["ok"]:
        return {"decision": "deny", "reasons": ["test deny"], "policy_version": "test"}
    if facts["namespace"]["env"] == "prod" and facts["tool"] != "get_status":
        return {"decision": "needs_approval", "reasons": ["prod write"], "policy_version": "test", "approver_team": ""}
    return {"decision": "allow", "reasons": ["ok"], "policy_version": "test"}


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.kube = FakeKube([deployment("dev", "checkout"), deployment("prod", "checkout")])
        self.audit, self.approver, self.facts = MemoryAudit(), Approver(), []
        self.gw = self.make()

    def make(self, decide=None):
        def record(facts):
            self.facts.append(copy.deepcopy(facts))
            return (decide or simple_policy)(facts)
        return actions.Gateway(self.kube, self.audit, self.approver, record, lambda facts: "three lines", clock=lambda: T)

    def replicas(self, ns="dev"):
        return self.kube.objects[("Deployment", ns, "checkout")]["spec"]["replicas"]

    def test_dev_scale_runs_and_is_recorded(self):
        args = {"namespace": "dev", "name": "checkout", "replicas": 6}
        r = self.gw.call("scale_deployment", args, "sre")
        self.assertEqual(r["result"]["status"], "executed")
        self.assertEqual(self.replicas(), 6)
        self.assertEqual(self.facts[0], {
            "tool": "scale_deployment", "args": args, "namespace": {"name": "dev", "env": "dev"},
            "time": "2026-10-21T15:30:00Z", "team": "sre", "current": {"replicas": 2},
            "dry_run": {"ok": True, "error": "", "pods_affected": 4, "capacity_change_pct": 200.0, "pdb_violations": []}})
        self.assertIn('-    "replicas": 2', r["diff"])
        rec = self.audit.records[0]
        self.assertEqual((rec["before"]["spec"]["replicas"], rec["after"]["spec"]["replicas"]), (2, 6))
        self.assertEqual(rec["undo_token"]["op"], "restore")
        self.assertEqual((rec["decision"], rec["policy_version"], rec["approver"]), ("allow", "test", None))
        self.assertTrue(r["action_id"].startswith("20261021T153000Z-"))
        self.assertRegex(r["action_id"], audit.ID)

    def test_arguments_reach_the_policy_exactly_as_sent(self):
        args = {"namespace": "prod", "name": "checkout", "replicas": 4, "approved_by": "break-glass"}
        r = self.gw.call("scale_deployment", args, "sre")
        self.assertEqual(self.facts[0]["args"], args)
        self.assertEqual(r["result"]["status"], "denied")
        self.assertEqual(self.replicas("prod"), 2)

    def test_unsafe_arguments_never_reach_the_cluster(self):
        for args in ({"namespace": "dev", "name": "checkout", "replicas": True},
                     {"namespace": "dev", "name": "../../api/v1/secrets", "replicas": 1},
                     {"namespace": "dev", "name": "checkout", "replicas": -1},
                     {"namespace": "dev", "name": "checkout"}):
            r = self.gw.call("scale_deployment", args, "sre")
            self.assertEqual(r["result"]["status"], "denied", args)
            self.assertTrue(r["dry_run"]["error"].startswith("not run: "), r["dry_run"])
        self.assertEqual(self.kube.sent, [])
        self.assertEqual([f["args"] for f in self.facts][1], {"namespace": "dev", "name": "../../api/v1/secrets", "replicas": 1})
        self.assertEqual(len(self.audit.records), 4)

    def test_other_kinds_and_namespaces_are_left_to_the_policy(self):
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s"}}
        r = self.gw.call("apply_manifest", {"namespace": "dev", "manifest": secret}, "sre")
        self.assertEqual((r["result"]["status"], r["dry_run"]["error"]), ("denied", "not run: kind Secret is not supported"))
        self.assertEqual(self.kube.sent, [])
        swap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c", "namespace": "prod"}}
        r = self.gw.call("apply_manifest", {"namespace": "dev", "manifest": swap}, "sre")
        self.assertEqual(r["result"]["status"], "denied")
        self.assertIn("does not match", r["dry_run"]["error"])  # sent as given, so the API server refuses it
        self.assertNotIn(("ConfigMap", "prod", "c"), self.kube.objects)

    def test_failed_dry_run_never_acts_even_if_the_policy_allows(self):
        gw = self.make(lambda facts: {"decision": "allow", "reasons": [], "policy_version": "buggy"})
        r = gw.call("scale_deployment", {"namespace": "dev", "name": "missing", "replicas": 3}, "sre")
        self.assertEqual(self.facts[0]["dry_run"], {"ok": False, "error": "HTTP 404: not found"})
        self.assertNotIn("current", self.facts[0])
        self.assertEqual(r["result"]["status"], "denied")

    def test_no_env_label_means_no_env_fact(self):
        self.kube.envs = {}
        self.gw.call("scale_deployment", {"namespace": "dev", "name": "checkout", "replicas": 3}, "sre")
        self.assertEqual(self.facts[0]["namespace"], {"name": "dev"})

    def test_prod_needs_approval_then_runs(self):
        r = self.gw.call("scale_deployment", {"namespace": "prod", "name": "checkout", "replicas": 4}, "sre")
        self.assertEqual((r["result"]["status"], r["approver"]["approver"], r["risk_summary"]), ("executed", "alice", "three lines"))
        self.assertEqual(self.replicas("prod"), 4)
        req = self.approver.requests[0]
        self.assertEqual((req["namespace"], req["name"], req["ai_risk_summary"], req["approver_team"]),
                         ("prod", "checkout", "three lines", ""))

    def test_rejected_or_timed_out_approval_does_nothing(self):
        self.approver.approved = False
        r = self.gw.call("restart_deployment", {"namespace": "prod", "name": "checkout"}, "sre")
        self.assertEqual(r["result"]["status"], "rejected")
        self.assertEqual(self.kube.objects[("Deployment", "prod", "checkout")]["metadata"]["generation"], 1)

    def test_object_changed_while_waiting_does_nothing(self):
        def approve_after_someone_scales(action_id, request, timeout_s):
            self.kube.send("Deployment", "prod", "checkout", {"type": "merge", "body": {"spec": {"replicas": 9}}}, False)
            return {"approved": True, "approver": "alice", "via": "test"}
        self.approver.decide = approve_after_someone_scales
        r = self.gw.call("scale_deployment", {"namespace": "prod", "name": "checkout", "replicas": 4}, "sre")
        self.assertEqual(r["result"]["status"], "failed")
        self.assertEqual(self.replicas("prod"), 9)

    def test_restart_sets_the_annotation(self):
        self.gw.call("restart_deployment", {"namespace": "dev", "name": "checkout"}, "sre")
        ann = self.kube.objects[("Deployment", "dev", "checkout")]["spec"]["template"]["metadata"]["annotations"]
        self.assertEqual(ann, {"kubectl.kubernetes.io/restartedAt": "2026-10-21T15:30:00Z"})
        self.assertEqual(self.facts[0]["dry_run"]["pods_affected"], 2)

    def test_rollout_undo_to_the_previous_or_a_given_revision(self):
        dep = self.kube.objects[("Deployment", "dev", "checkout")]
        dep["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = "3"

        def rs(rev, image):
            d = deployment("dev", f"checkout-{rev}", revision=rev)
            d["metadata"]["ownerReferences"] = [{"uid": "uid-checkout"}]
            d["spec"]["template"]["metadata"]["labels"]["pod-template-hash"] = f"h{rev}"
            d["spec"]["template"]["spec"]["containers"][0]["image"] = image
            return d
        self.kube.replicasets = [rs(1, "app:v0"), rs(2, "app:v1"), rs(3, "app:v2")]

        def image():
            return self.kube.objects[("Deployment", "dev", "checkout")]["spec"]["template"]["spec"]["containers"][0]["image"]

        def controller_caught_up():
            d = self.kube.objects[("Deployment", "dev", "checkout")]
            d["status"]["observedGeneration"] = d["metadata"]["generation"]
        r = self.gw.call("rollout_undo", {"namespace": "dev", "name": "checkout"}, "sre")
        self.assertEqual((r["result"]["status"], image()), ("executed", "app:v1"))
        tmpl = self.kube.objects[("Deployment", "dev", "checkout")]["spec"]["template"]
        self.assertNotIn("pod-template-hash", tmpl["metadata"]["labels"])
        controller_caught_up()
        r = self.gw.call("rollout_undo", {"namespace": "dev", "name": "checkout", "to_revision": 1}, "sre")
        self.assertEqual((r["result"]["status"], image()), ("executed", "app:v0"))
        controller_caught_up()
        r = self.gw.call("rollout_undo", {"namespace": "dev", "name": "checkout", "to_revision": 7}, "sre")
        self.assertEqual(r["dry_run"]["error"], "not run: no revision 7")

    def test_rollout_undo_waits_for_the_last_change_to_be_recorded(self):
        self.kube.objects[("Deployment", "dev", "checkout")]["metadata"]["generation"] = 2  # status still says 1
        r = self.gw.call("rollout_undo", {"namespace": "dev", "name": "checkout"}, "sre")
        self.assertEqual(r["result"]["status"], "denied")
        self.assertIn("isn't recorded yet", r["dry_run"]["error"])

    def test_undo_restores_the_state_before(self):
        act = self.gw.call("scale_deployment", {"namespace": "dev", "name": "checkout", "replicas": 6}, "sre")
        undo = self.gw.call("undo_action", {"action_id": act["action_id"]}, "sre")
        self.assertEqual(undo["result"]["status"], "executed")
        self.assertEqual(self.replicas(), 2)
        f = self.facts[-1]
        self.assertEqual((f["args"], f["namespace"], f["dry_run"]["capacity_change_pct"]),
                         ({"action_id": act["action_id"]}, {"name": "dev", "env": "dev"}, -66.7))
        self.assertEqual(self.audit.records[-1]["undo_of"], act["action_id"])
        again = self.gw.call("undo_action", {"action_id": act["action_id"]}, "sre")
        self.assertEqual(again["result"]["status"], "denied")  # the first undo changed the object

    def test_undo_refused_for_another_team_or_a_later_change(self):
        act = self.gw.call("scale_deployment", {"namespace": "dev", "name": "checkout", "replicas": 6}, "sre")
        self.assertEqual(self.gw.call("undo_action", {"action_id": act["action_id"]}, "payments")["reasons"],
                         ["only the team that made an action can undo it"])
        self.kube.send("Deployment", "dev", "checkout", {"type": "merge", "body": {"spec": {"replicas": 8}}}, False)
        r = self.gw.call("undo_action", {"action_id": act["action_id"]}, "sre")
        self.assertIn("changed after that action", r["reasons"][0])
        self.assertEqual(self.replicas(), 8)

    def test_undo_of_a_create_deletes_it(self):
        cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "flags"}, "data": {"a": "1"}}
        act = self.gw.call("apply_manifest", {"namespace": "dev", "manifest": cm}, "sre")
        self.assertEqual(act["result"]["status"], "executed")
        self.assertEqual(self.facts[0]["args"]["manifest"], cm)
        self.assertEqual(self.kube.objects[("ConfigMap", "dev", "flags")]["metadata"]["namespace"], "dev")
        self.assertEqual(self.gw.call("undo_action", {"action_id": act["action_id"]}, "sre")["result"]["status"], "executed")
        self.assertNotIn(("ConfigMap", "dev", "flags"), self.kube.objects)

    def test_undo_of_a_configmap_change_restores_data(self):
        self.kube.objects[("ConfigMap", "dev", "flags")] = {"apiVersion": "v1", "kind": "ConfigMap", "data": {"a": "1"},
                                                          "metadata": {"name": "flags", "namespace": "dev", "uid": "u", "resourceVersion": "5"}}
        cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "flags"}, "data": {"a": "2"}}
        act = self.gw.call("apply_manifest", {"namespace": "dev", "manifest": cm}, "sre")
        self.assertEqual(self.kube.objects[("ConfigMap", "dev", "flags")]["data"], {"a": "2"})
        self.gw.call("undo_action", {"action_id": act["action_id"]}, "sre")
        self.assertEqual(self.kube.objects[("ConfigMap", "dev", "flags")]["data"], {"a": "1"})

    def test_audit_failure_is_reported(self):
        self.audit.write = mock.Mock(side_effect=OSError("disk full"))
        r = self.gw.call("scale_deployment", {"namespace": "dev", "name": "checkout", "replicas": 3}, "sre")
        self.assertEqual(r["result"]["status"], "executed")
        self.assertIn("audit record was not written", r["result"]["audit_error"])

    def test_get_status(self):
        r = self.gw.call("get_status", {"namespace": "dev"}, "sre")
        self.assertEqual(r["decision"], "allow")
        self.assertEqual([(d["name"], d["replicas"], d["ready"]) for d in r["deployments"]], [("checkout", 2, 2)])
        self.assertEqual(self.facts[0], {"tool": "get_status", "args": {"namespace": "dev"},
                                         "namespace": {"name": "dev", "env": "dev"}, "time": "2026-10-21T15:30:00Z", "team": "sre"})
        self.assertEqual(self.audit.records, [])
        denied = self.make(lambda facts: {"decision": "deny", "reasons": ["no env label"], "policy_version": "t"})
        self.assertNotIn("deployments", denied.call("get_status", {"namespace": "dev"}, "sre"))
        buggy = self.make(lambda facts: {"decision": "allow", "reasons": [], "policy_version": "t"})
        self.assertNotIn("deployments", buggy.call("get_status", {"namespace": "../x"}, "sre"))

    def test_unknown_tool(self):
        self.assertEqual(self.gw.call("delete_namespace", {}, "sre")["decision"], "deny")


class Helpers(unittest.TestCase):
    def test_team_for(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({hashlib.sha256(b"tok-1").hexdigest(): "sre"}, f)
        self.addCleanup(os.remove, f.name)
        self.assertEqual(actions.team_for("Bearer tok-1", f.name), "sre")
        for header in ("Bearer tok-2", "tok-1", "Basic tok-1", "Bearer ", None):
            self.assertIsNone(actions.team_for(header, f.name), header)
        self.assertIsNone(actions.team_for("Bearer tok-1", "/no/such/file"))

    def test_clock_reads_the_test_only_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("2026-10-23T18:00:00-05:00\n")
        self.addCleanup(os.remove, f.name)
        with mock.patch.dict(os.environ, {"TEST_ONLY_CLOCK_FILE": f.name}):
            self.assertEqual(actions.now(), datetime.datetime(2026, 10, 23, 23, 0, tzinfo=datetime.timezone.utc))
        with mock.patch.dict(os.environ, {"TEST_ONLY_CLOCK_FILE": "/no/such/file"}):
            self.assertLess(abs((actions.now() - datetime.datetime.now(datetime.timezone.utc)).total_seconds()), 5)

    def test_schemas_match_the_policy_data(self):
        """The arguments each tool advertises are the ones policies/data.json allows (once it's in the repo)."""
        data = os.path.join(os.path.dirname(__file__), "..", "policies", "data.json")
        for tool in actions.TOOLS:
            s = actions.schema(tool)
            self.assertFalse(s["additionalProperties"])
            self.assertTrue(set(s["required"]) <= set(s["properties"]), tool)
        if os.path.exists(data):
            with open(data) as f:
                tools = json.load(f)["gateway_config"]["tools"]
            self.assertEqual(set(tools), set(actions.TOOLS))
            for name, t in tools.items():
                s = actions.schema(name)
                self.assertEqual((set(t["required"]), set(t["required"]) | set(t["optional"])),
                                 (set(s["required"]), set(s["properties"])), name)


if __name__ == "__main__":
    unittest.main()
