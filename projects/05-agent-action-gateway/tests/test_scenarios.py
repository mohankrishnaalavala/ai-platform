"""Checks the scenario files in evals/scenarios: required fields, the 20/15/15 split and unique names.

The files are YAML written in JSON syntax, so the standard library's json module reads them (CI's unit-test step
installs nothing) and any YAML parser reads them too. tests/check_scenarios.py checks their expected decisions
against the policy.

One scenario per file. Every scenario starts from evals/base.yaml.
  name              the file name without .yaml
  group             safe (expects allow), risky (needs_approval) or dangerous (deny)
  description       what the call does, in one or two sentences
  prompt_injection  true when the call is what an agent fooled by text it read would send (optional)
  team              the requester team: the runner calls with an agent token that maps to it
  time              the gateway's clock, set through a test-only value, never by the agent
  setup             Kubernetes objects applied after the base; one with the same kind and name replaces it
  call              {"tool": ..., "arguments": {...}}: the MCP tool call, sent exactly as written
  expect            decision; reasons: keywords that must each appear in the reasons (any case);
                    dry_run (optional): what the live dry run must report (ok, pdb_violations, pods_affected);
                    approver_team, irreversible, policy_version (optional): must equal the policy's result
"""
import datetime
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DECISION = {"safe": "allow", "risky": "needs_approval", "dangerous": "deny"}
COUNTS = {"safe": 20, "risky": 15, "dangerous": 15}
KEYS = {"name", "group", "description", "team", "time", "setup", "call", "expect"}
DRY_RUN_KEYS = {"ok", "pdb_violations", "pods_affected"}
RESULT_FIELDS = {"approver_team": str, "irreversible": bool, "policy_version": str}  # optional in expect


def load_base():
    return json.loads((ROOT / "evals" / "base.yaml").read_text())["objects"]


def load_scenarios():
    return {p: json.loads(p.read_text()) for p in sorted((ROOT / "evals" / "scenarios").glob("*.yaml"))}


def is_text(v):
    return isinstance(v, str) and v.strip() != ""


def is_object(o):
    return isinstance(o, dict) and is_text(o.get("apiVersion")) and is_text(o.get("kind")) \
        and is_text(o.get("metadata", {}).get("name"))


class Scenarios(unittest.TestCase):
    def setUp(self):
        self.scenarios = load_scenarios()

    def test_counts(self):
        groups = [s.get("group") for s in self.scenarios.values()]
        self.assertEqual(len(groups), 50)
        self.assertEqual({g: groups.count(g) for g in COUNTS}, COUNTS)

    def test_names_unique_and_match_file_names(self):
        names = [s.get("name") for s in self.scenarios.values()]
        self.assertEqual(len(set(names)), len(names))
        for path, s in self.scenarios.items():
            self.assertEqual(s.get("name"), path.stem)

    def test_fields(self):
        for path, s in self.scenarios.items():
            with self.subTest(path.name):
                self.assertEqual(set(s) - {"prompt_injection"}, KEYS)
                self.assertIn(s.get("prompt_injection", True), [True])
                self.assertIn(s["group"], DECISION)
                self.assertTrue(is_text(s["description"]) and is_text(s["team"]))
                self.assertIsNotNone(datetime.datetime.fromisoformat(s["time"]).tzinfo, "time needs a UTC offset")
                self.assertIsInstance(s["setup"], list)
                self.assertTrue(all(is_object(o) for o in s["setup"]), "each setup item is a Kubernetes object")
                self.assertEqual(set(s["call"]), {"tool", "arguments"})
                self.assertTrue(is_text(s["call"]["tool"]))
                self.assertIsInstance(s["call"]["arguments"], dict)
                expect = s["expect"]
                self.assertLessEqual(set(expect), {"decision", "reasons", "dry_run", *RESULT_FIELDS})
                self.assertEqual(expect["decision"], DECISION[s["group"]])
                self.assertTrue(expect["reasons"] and all(is_text(k) for k in expect["reasons"]))
                self.assertLessEqual(set(expect.get("dry_run", {})), DRY_RUN_KEYS)
                for field, kind in RESULT_FIELDS.items():
                    if field in expect:
                        self.assertIsInstance(expect[field], kind, field)

    def test_dangerous_group_covers_the_required_cases(self):
        dangerous = [s for s in self.scenarios.values() if s["group"] == "dangerous"]
        self.assertTrue(any(s.get("prompt_injection") for s in dangerous))
        keywords = " ".join(k.lower() for s in dangerous for k in s["expect"]["reasons"])
        for case in ["0 replicas in prod", "privileged", "freeze", "no env label", "poddisruptionbudget",
                     "blast radius"]:
            self.assertIn(case, keywords)

    def test_base_has_the_demo_namespaces(self):
        objects = {(o["kind"], o["metadata"].get("namespace"), o["metadata"]["name"]): o for o in load_base()}
        for env in ["dev", "staging", "prod"]:
            self.assertEqual(objects[("Namespace", None, env)]["metadata"]["labels"]["env"], env)
            self.assertIn(("Deployment", env, "checkout"), objects)
            self.assertIn(("PodDisruptionBudget", env, "checkout"), objects)


if __name__ == "__main__":
    unittest.main()
