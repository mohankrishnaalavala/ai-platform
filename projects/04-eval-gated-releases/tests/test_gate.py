"""P4-06: the eval gate on fixture results files. A good one passes; a stale hash, a below-threshold one, one that
doesn't cover the dataset, and one without a committed baseline fail.
The committed fixtures carry old hashes; a test writes in the hash of today's files before checking a fresh one.
Each test runs with the fixture dataset as the golden set and a temporary folder as results/. Standard library only.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "evals"))
import gate
import run

FIXTURES = HERE / "fixtures" / "results"
CONFIG = "projects/04-eval-gated-releases/tests/fixtures/config.yaml"
DATASET = "projects/04-eval-gated-releases/tests/fixtures/dataset"
THRESHOLDS = run.load_thresholds()
CONFIG_SHA = run.sha((run.ROOT / CONFIG).read_bytes())


class Gate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.results = Path(tmp.name)
        for patch in (mock.patch.object(gate, "RESULTS", f"{tmp.name}/"), mock.patch.object(gate, "DATASET", DATASET)):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = self.results / run.config_hash(run.hash_parts(CONFIG, DATASET, "judge"))

    def commit(self, name, date="2026-10-21T12:00:00Z", fresh=True, edit=None):
        """A fixture results file, written to results/<its hash>/ as if committed. fresh: with today's hash."""
        r = json.loads((FIXTURES / name).read_text())
        if fresh:
            r["hash_parts"] = run.hash_parts(r["config"], r["dataset"], r["judge"])
            r["config_hash"] = run.config_hash(r["hash_parts"])
        r["date"] = date
        if edit:
            edit(r)
        path = self.results / r["config_hash"] / f"{date}-{name}"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(r))
        return path

    def baseline(self):
        return self.commit("baseline.json", date="2026-10-21T11:00:00Z")

    def check(self, path, need_baseline=True):
        return gate.verify(path, THRESHOLDS, need_baseline, gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, path))

    def test_good_results_pass(self):
        self.baseline()
        failures, md = self.check(self.commit("good.json"))
        self.assertEqual(failures, [])
        self.assertIn("| qa | judge_avg | 4.00 | 4.00 | 4.00 | ≥ baseline − 0.2 | pass |", md)

    def test_a_stale_hash_fails(self):
        failures, _ = self.check(self.commit("stale.json", fresh=False))
        self.assertTrue(failures[0].startswith("stale: config hash 3f2a9c1b7d4e6f80"), failures)

    def test_below_threshold_fails(self):
        self.baseline()
        failures, md = self.check(self.commit("below.json"))
        self.assertEqual([f.split(":")[0] for f in failures], ["qa", "safety"])  # 3.5 < 4.0 - 0.2; 50% < 100%
        self.assertIn("FAIL", md)

    def test_a_fallback_answer_fails(self):
        self.baseline()
        failures, _ = self.check(self.commit("good.json", edit=lambda r: r["cases"][0].update(fallbacks="1")))
        self.assertEqual(len(failures), 1)
        self.assertIn("answered by a fallback", failures[0])

    # Review item 1: results must cover the real dataset, every case and round, with the judge alias.
    def test_results_must_cover_the_dataset(self):
        self.baseline()
        for edit, problem in ((lambda r: r.update(cases=r["cases"][2:5]), "cases aren't"),       # 3 passing cases
                              (lambda r: r["cases"].append(dict(r["cases"][2])), "cases aren't"),  # a case twice
                              (lambda r: r["cases"][4].update(category="instruction"), "cases aren't"),  # relabelled
                              (lambda r: r.update(rounds=2), "cases aren't"),                    # a round missing
                              (lambda r: r.update(judge="chat-hosted"), "judged by the alias chat-hosted")):
            failures, _ = self.check(self.commit("good.json", edit=edit))
            self.assertTrue(any(problem in f for f in failures), failures)
        with mock.patch.object(gate, "DATASET", "projects/04-eval-gated-releases/evals/datasets/v1"):
            failures, _ = self.check(self.commit("good.json"))  # built on the fixture set, not the golden set
        self.assertIn("not projects/04-eval-gated-releases/evals/datasets/v1", failures[0])

    # Review item 2: the baseline is derived, VALID, complete, another file, never the PR's pick.
    def test_the_baseline_is_derived_not_picked(self):
        good = self.commit("good.json")
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good))  # never itself
        failures, _ = self.check(good)  # the file's own "baseline" field names baseline.json: ignored
        self.assertEqual([f.split(":")[0] for f in failures], ["qa", "instruction"])
        self.assertTrue(all(f.endswith("FAIL: no baseline") for f in failures), failures)
        self.commit("baseline.json", date="2026-10-21T11:00:00Z", edit=lambda r: r["cases"][0].update(fallbacks="1"))
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good))  # an INVALID one doesn't count
        self.commit("baseline.json", date="2026-10-21T10:00:00Z", edit=lambda r: r.update(cases=r["cases"][:3]))
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good))  # nor an incomplete one
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, "f" * 64, good))  # nor another config version
        self.assertIsNone(gate.baseline_for("chat-hosted", CONFIG, CONFIG_SHA, good))  # nor another target
        base = self.baseline()
        self.assertEqual(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good)[0], base)
        self.assertEqual(self.check(good)[0], [])

    def test_the_served_config_needs_a_baseline_a_results_file_doesnt(self):
        good = self.commit("good.json")
        self.assertEqual(len(self.check(good, need_baseline=True)[0]), 2)
        self.assertEqual(self.check(good, need_baseline=False)[0], [])

    def test_newest_finds_results_at_the_current_hash(self):
        self.commit("good.json", date="2026-10-21T10:00:00Z")
        newer = self.commit("good.json", date="2026-10-21T11:00:00Z")
        self.assertEqual(gate.newest("chat-default", CONFIG), newer)
        self.assertIsNone(gate.newest("chat-hosted", CONFIG))
        self.assertIsNone(gate.newest("chat-default", "config/models/qwen2.5-7b-fp8.yaml"))  # nothing at its hash

    def test_which_configs_are_checked(self):
        served = gate.served((run.ROOT / gate.SERVES).read_text())
        self.assertEqual(served, "config/models/qwen2.5-7b-bf16.yaml")
        self.assertEqual(gate.served("x\nconfig=${CONFIG:-config/models/new.yaml}\n"), "config/models/new.yaml")
        self.assertEqual(gate.targets(["config/models/qwen3-8b.yaml"], served, served), {})  # a new, unserved config
        self.assertEqual(gate.targets([gate.SERVES], "config/models/new.yaml", served),
                         {("chat-default", "config/models/new.yaml"): "served"})
        self.assertEqual(gate.targets([served], served, served), {("chat-default", served): "served"})
        # Review item 3: a change to the gate checks the served config, with a baseline required.
        self.assertEqual(gate.targets(["projects/04-eval-gated-releases/evals/judge.md"], served, served),
                         {("chat-default", served): "gate"})
        path = self.commit("good.json")
        self.assertEqual(gate.targets([str(path)], served, served), {("chat-default", CONFIG): "results"})

    def run_gate(self, changed, deleted=()):
        return gate.gate(changed, list(deleted), CONFIG, CONFIG, lambda path: CONFIG_SHA, THRESHOLDS)

    # Review item 3, end to end: after a gate change, stale results fail instead of "Nothing to check".
    def test_a_gate_change_fails_on_stale_results(self):
        judge_md = "projects/04-eval-gated-releases/evals/judge.md"
        failed, text = self.run_gate([judge_md])
        self.assertFalse(failed)  # never evaluated: nothing to go stale
        self.assertIn("never evaluated", text)
        self.commit("stale.json", fresh=False)
        failed, text = self.run_gate([judge_md])
        self.assertTrue(failed)
        self.assertIn("stale or missing", text)
        self.baseline()
        self.commit("good.json")  # both runs of the noise band, at today's hash
        self.assertEqual(self.run_gate([judge_md])[0], False)

    def test_a_results_only_change_is_checked_and_nothing_else_passes(self):
        self.baseline()
        self.assertFalse(self.run_gate([str(self.commit("good.json", date="2026-10-21T13:00:00Z"))])[0])
        self.assertTrue(self.run_gate([str(self.commit("below.json", date="2026-10-21T14:00:00Z"))])[0])
        failed, text = self.run_gate(["README.md"])
        self.assertEqual((failed, "Nothing to check" in text), (False, True))

    # Review item 4, and the comment step: results-only PRs run the gate; a read-only token can't turn it red.
    def test_evals_yml_triggers_on_results_and_the_comment_cant_fail_the_job(self):
        workflow = (run.ROOT / ".github/workflows/evals.yml").read_text()
        self.assertIn("      - projects/04-eval-gated-releases/results/**", workflow)
        self.assertRegex(workflow, r"gh pr comment [^\n]*\\\n\s+\|\| echo \"::warning::")

    def test_deleting_a_results_file_fails(self):
        failed, text = self.run_gate([], deleted=[f"{self.results}/abc/old.json"])
        self.assertTrue(failed)
        self.assertIn("must not be deleted", text)


if __name__ == "__main__":
    unittest.main()
