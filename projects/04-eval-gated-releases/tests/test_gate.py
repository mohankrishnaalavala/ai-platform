"""P4-06: the eval gate on fixture results files. A good one passes, a stale hash fails, a below-threshold one fails.
The committed fixtures carry old hashes; a test writes in the hash of today's files before checking a fresh one.
Standard library only."""
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

RESULTS = HERE / "fixtures" / "results"
THRESHOLDS = run.load_thresholds()


def fresh(name, folder, **changes):
    """A fixture results file with the config hash of the files in this commit, written to folder."""
    r = json.loads((RESULTS / name).read_text())
    parts = run.hash_parts(r["config"], r["dataset"])
    r.update(hash_parts=parts, config_hash=run.config_hash(parts), **changes)
    path = Path(folder) / name
    path.write_text(json.dumps(r))
    return path


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.baseline = str(fresh("baseline.json", self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_good_results_pass(self):
        failures, md = gate.verify(fresh("good.json", self.tmp.name, baseline=self.baseline), THRESHOLDS, True)
        self.assertEqual(failures, [])
        self.assertIn("| qa | judge_avg | 4.00 | 4.00 | 4.00 | ≥ baseline − 0.2 | pass |", md)

    def test_a_stale_hash_fails(self):
        failures, _ = gate.verify(RESULTS / "stale.json", THRESHOLDS, True)
        self.assertTrue(failures[0].startswith("stale: config hash 3f2a9c1b7d4e6f80"), failures)

    def test_below_threshold_fails(self):
        failures, md = gate.verify(fresh("below.json", self.tmp.name, baseline=self.baseline), THRESHOLDS, True)
        self.assertEqual([f.split(":")[0] for f in failures], ["qa", "safety"])  # 3.5 < 4.0 - 0.2; 50% < 100%
        self.assertIn("FAIL", md)

    def test_a_fallback_answer_fails(self):
        path = fresh("good.json", self.tmp.name, baseline=self.baseline)
        r = json.loads(path.read_text())
        r["cases"][0]["fallbacks"] = "1"
        path.write_text(json.dumps(r))
        failures, _ = gate.verify(path, THRESHOLDS, True)
        self.assertEqual(len(failures), 1)
        self.assertIn("answered by a fallback", failures[0])

    def test_the_served_config_needs_a_baseline(self):
        path = fresh("good.json", self.tmp.name, baseline=None)
        self.assertEqual(len(gate.verify(path, THRESHOLDS, True)[0]), 2)
        self.assertEqual(gate.verify(path, THRESHOLDS, False)[0], [])

    def test_newest_finds_results_at_the_current_hash(self):
        config, dataset = "projects/04-eval-gated-releases/tests/fixtures/config.yaml", "projects/04-eval-gated-releases/tests/fixtures/dataset"
        folder = Path(self.tmp.name) / run.config_hash(run.hash_parts(config, dataset))
        folder.mkdir()
        for name, date in (("a.json", "2026-10-21T10:00:00Z"), ("b.json", "2026-10-21T11:00:00Z")):
            (folder / name).write_text(json.dumps({"config": config, "date": date}))
        with mock.patch.object(gate, "RESULTS", self.tmp.name), mock.patch.object(gate, "DATASET", dataset):
            self.assertEqual(gate.newest(config).name, "b.json")
            self.assertIsNone(gate.newest("config/models/qwen2.5-7b-fp8.yaml"))  # nothing at its hash

    def test_which_configs_are_checked(self):
        served = gate.served((run.ROOT / gate.SERVES).read_text())
        self.assertEqual(served, "config/models/qwen2.5-7b-bf16.yaml")
        self.assertTrue((run.ROOT / served).exists())
        self.assertEqual(gate.served('x\nconfig=${CONFIG:-config/models/new.yaml}\n'), "config/models/new.yaml")
        self.assertEqual(gate.targets(["config/models/qwen3-8b.yaml"], served, served), {})  # a new, unserved config
        self.assertEqual(gate.targets([gate.SERVES], "config/models/new.yaml", served), {"config/models/new.yaml": True})
        self.assertEqual(gate.targets([served], served, served), {served: True})  # the served file itself changed
        fixture = "projects/04-eval-gated-releases/tests/fixtures/results/good.json"
        with mock.patch.object(gate, "RESULTS", "projects/04-eval-gated-releases/tests/fixtures/results/"):
            self.assertEqual(gate.targets([fixture], served, served),
                             {"projects/04-eval-gated-releases/tests/fixtures/config.yaml": False})


if __name__ == "__main__":
    unittest.main()
