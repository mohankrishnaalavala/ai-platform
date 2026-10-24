"""P4-06: the eval gate on fixture results files, on both paths.
Enforced (the served config): a good file passes; a stale hash, a below-threshold file, one that doesn't cover the
dataset, and one without a committed baseline fail.
Report only (any other results file): an integrity failure fails; scores below a threshold are only reported.
The committed fixtures carry old hashes; a test writes in the hash of today's files before checking a fresh one.
Each test runs with the fixture dataset as the golden set and a temporary folder as results/. Standard library only.
"""
import contextlib
import io
import json
import os
import subprocess
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
ANY = lambda path, r: True  # accept any baseline file; gate() narrows this


class Gate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.results = Path(tmp.name)
        for patch in (mock.patch.object(gate, "RESULTS", f"{tmp.name}/"), mock.patch.object(gate, "DATASET", DATASET)):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = self.results / run.config_hash(run.hash_parts(CONFIG, DATASET, "judge"))
        self.base = {}  # the results files in the base commit, as gate.main reads them: {repo path: results}

    def on_main(self, path):
        """Mark a results file as already in the base commit."""
        self.base[run.rel(path)] = json.loads(path.read_text())
        return path

    def commit(self, name, date="2026-10-21T12:00:00Z", fresh=True, edit=None):
        """A fixture results file, written to results/<its hash>/ as this PR adds it. fresh: with today's hash."""
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
        """The baseline run, already on main."""
        return self.on_main(self.commit("baseline.json", date="2026-10-21T11:00:00Z"))

    def check(self, path, enforce=True):
        return gate.verify(path, THRESHOLDS, enforce, gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, path, ANY))

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
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good, ANY))  # never itself
        failures, _ = self.check(good)  # the file's own "baseline" field names baseline.json: ignored
        self.assertEqual([f.split(":")[0] for f in failures], ["qa", "instruction"])
        self.assertTrue(all(f.endswith("FAIL: no baseline") for f in failures), failures)
        self.commit("baseline.json", date="2026-10-21T11:00:00Z", edit=lambda r: r["cases"][0].update(fallbacks="1"))
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good, ANY))  # an INVALID one doesn't count
        self.commit("baseline.json", date="2026-10-21T10:00:00Z", edit=lambda r: r.update(cases=r["cases"][:3]))
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good, ANY))  # nor an incomplete one
        self.assertIsNone(gate.baseline_for("chat-default", CONFIG, "f" * 64, good, ANY))  # nor another config version
        self.assertIsNone(gate.baseline_for("chat-hosted", CONFIG, CONFIG_SHA, good, ANY))  # nor another target
        base = self.baseline()
        self.assertEqual(gate.baseline_for("chat-default", CONFIG, CONFIG_SHA, good, ANY)[0], base)
        self.assertEqual(self.check(good)[0], [])

    def test_enforced_needs_a_baseline_report_only_doesnt(self):
        good = self.commit("good.json")
        self.assertEqual(len(self.check(good, enforce=True)[0]), 2)
        self.assertEqual(self.check(good, enforce=False)[0], [])

    def test_newest_finds_results_at_the_current_hash(self):
        self.commit("good.json", date="2026-10-21T10:00:00Z")
        newer = self.commit("good.json", date="2026-10-21T11:00:00Z")
        self.assertEqual(gate.newest("chat-default", CONFIG), newer)
        self.assertIsNone(gate.newest("chat-hosted", CONFIG))
        self.assertIsNone(gate.newest("chat-default", "config/models/qwen2.5-7b-fp8.yaml"))  # nothing at its hash

    def test_which_configs_are_checked(self):
        served = gate.served((run.ROOT / gate.SERVES).read_text())
        self.assertTrue((run.ROOT / served).is_file(), served)  # whichever model is served, not a fixed one
        self.assertEqual(gate.served("x\nconfig=${CONFIG:-config/models/new.yaml}\n"), "config/models/new.yaml")
        self.assertEqual(gate.enforced(["config/models/qwen3-8b.yaml"], served, served), {})  # a new, unserved config
        self.assertEqual(gate.enforced([gate.SERVES], "config/models/new.yaml", served),
                         {("chat-default", "config/models/new.yaml"): "served"})
        self.assertEqual(gate.enforced([served], served, served), {("chat-default", served): "served"})
        # Review item 3: a change to the gate checks the served config, with a baseline required.
        self.assertEqual(gate.enforced(["projects/04-eval-gated-releases/evals/judge.md"], served, served),
                         {("chat-default", served): "gate"})
        self.assertEqual(gate.enforced([gate.JUDGE_VALUES], served, served),  # the judge's model is in the hash
                         {("chat-default", served): "gate"})
        self.assertIn(gate.JUDGE_VALUES, [run.rel(p) for p in run.GATEWAY_VALUES])
        path = self.commit("good.json")  # a results file alone enforces nothing; it is checked as report only
        self.assertEqual(gate.enforced([str(path)], served, served), {})
        self.assertEqual(gate.changed_results([str(path), "README.md", f"{self.results}/gone.json"]), [path])

    def run_gate(self, changed, touched=(), thresholds=THRESHOLDS):
        return gate.gate(changed, list(touched), CONFIG, base_served=CONFIG, sha_at_base=lambda path: CONFIG_SHA,
                         base_results=self.base, thresholds=thresholds)

    # Enforced: a PR that changes the served config is held to the thresholds (the bad candidate stays red).
    def test_the_served_config_is_enforced(self):
        self.baseline()
        self.commit("below.json")
        failed, text = self.run_gate([CONFIG])
        self.assertTrue(failed)
        self.assertIn(f"### `chat-default` with `{CONFIG}` (enforced): FAIL", text)
        self.commit("good.json", date="2026-10-21T13:00:00Z")  # a newer, passing run
        failed, text = self.run_gate([CONFIG])
        self.assertFalse(failed)
        self.assertIn("(enforced): pass", text)

    # Review item 3, end to end: after a gate change, stale results fail instead of "Nothing to check".
    def test_a_gate_change_fails_on_stale_results(self):
        judge_md = "projects/04-eval-gated-releases/evals/judge.md"
        failed, text = self.run_gate([judge_md])
        self.assertFalse(failed)  # never evaluated: nothing to go stale
        self.assertIn("never evaluated", text)
        self.on_main(self.commit("stale.json", fresh=False))
        failed, text = self.run_gate([judge_md])
        self.assertTrue(failed)
        self.assertIn("stale or missing", text)
        self.commit("baseline.json", date="2026-10-21T11:00:00Z")  # both runs of the noise band, in this PR
        self.commit("good.json")
        self.assertEqual(self.run_gate([judge_md])[0], False)

    # Attack: "never evaluated" is read from the base commit, so results the PR leaves out of its tree don't count.
    def test_never_evaluated_comes_from_the_base_commit(self):
        self.base["projects/04-eval-gated-releases/results/abc/old.json"] = {"target": "chat-default", "config": CONFIG}
        failed, text = self.run_gate(["projects/04-eval-gated-releases/evals/judge.md"])
        self.assertTrue(failed)
        self.assertIn("stale or missing", text)

    # Attack: relabelling (or rewriting, moving, deleting) a committed results file fails; results are append-only.
    def test_relabelling_a_committed_file_fails(self):
        self.baseline()
        committed = self.on_main(self.commit("below.json"))
        relabelled = json.loads(committed.read_text())
        relabelled["cases"][4]["category"] = "instruction"  # hide the failed refusal from the safety gate
        committed.write_text(json.dumps(relabelled))
        failed, text = self.run_gate([str(committed)], touched=[str(committed)])
        self.assertTrue(failed)
        self.assertIn("append-only", text)
        moved = f"{self.results}/elsewhere/below.json"  # a move, without rename detection: delete + add
        failed, text = self.run_gate([str(committed), moved], touched=[str(committed)])
        self.assertTrue(failed)
        self.assertIn("append-only", text)

    # Attack: a later, weak baseline added in a served change's PR is ignored; only main's baseline counts.
    def test_a_weak_baseline_added_by_a_served_change_is_ignored(self):
        real = self.baseline()  # qa 4.0, on main
        self.commit("baseline.json", date="2026-10-21T11:30:00Z",  # qa 3.0, newer, added by this PR
                    edit=lambda r: [c.update(score=3) for c in r["cases"] if c["kind"] == "judge"])
        self.commit("below.json", edit=lambda r: r["cases"][4].update(passed=True))  # qa 3.5, safety fixed
        failed, text = self.run_gate([CONFIG])
        self.assertTrue(failed)  # 3.5 < 4.0 - 0.2; against the weak one it would pass (3.5 >= 3.0 - 0.2)
        self.assertIn(f"- Baseline: `{run.rel(real)}`", text)
        self.assertIn("qa: judge_avg 3.50, gate ≥ baseline − 0.2: FAIL", text)

    # A gate change may bring its own baseline (the noise band), but only a run older than the candidate.
    def test_a_gate_change_baseline_must_be_older_than_the_candidate(self):
        self.on_main(self.commit("stale.json", fresh=False))  # evaluated before
        self.commit("baseline.json")  # same recorded time as the candidate: not older
        self.commit("good.json")
        failed, text = self.run_gate(["projects/04-eval-gated-releases/evals/judge.md"])
        self.assertTrue(failed)
        self.assertIn("FAIL: no baseline", text)

    # Attack: loosening thresholds in the same PR is ignored; the base commit's thresholds apply, with a note.
    def test_loosened_thresholds_in_the_pr_are_ignored(self):
        self.baseline()
        self.commit("below.json")
        failed, text = self.run_gate([CONFIG, gate.THRESHOLDS])  # the PR's loose file is never read
        self.assertTrue(failed)
        self.assertIn("applies only after merge, and needs the project owner's approval", text)
        for missing in (None, {}):  # no thresholds at the base commit: nothing can pass as enforced
            failed, text = self.run_gate([CONFIG], thresholds=missing)
            self.assertTrue(failed)
            self.assertIn("nothing to enforce", text)

    def test_main_reads_the_base_commit_from_git(self):
        base_limits = run.parse_thresholds((run.ROOT / gate.THRESHOLDS).read_text().replace("value: 100", "value: 77"))
        files = {gate.SERVES: b"x\nconfig=${CONFIG:-config/models/base.yaml}\n",  # differs from the working tree's
                 gate.THRESHOLDS: (run.ROOT / gate.THRESHOLDS).read_text().replace("value: 100", "value: 77").encode(),
                 f"{gate.RESULTS}abc/old.json": b'{"target": "chat-default", "config": "x"}'}
        calls = []

        def fake_git(*args):
            calls.append(args)
            if args[0] == "show":
                path = args[1].split(":", 1)[1]
                if path not in files:
                    raise gate.subprocess.CalledProcessError(128, "git")
                return files[path]
            if args[0] == "ls-tree":
                return f"{gate.RESULTS}abc/old.json\n".encode()
            return b"" if "--diff-filter=DM" in args else f"{gate.THRESHOLDS}\n".encode()

        with mock.patch.object(gate, "git", fake_git), mock.patch.object(gate, "gate", return_value=(False, "")) as g, \
                contextlib.redirect_stdout(io.StringIO()):
            gate.main(["BASE"])
        kw = g.call_args.kwargs
        self.assertEqual(kw["thresholds"], base_limits)  # the base commit's file, not the working tree's
        self.assertEqual(kw["thresholds"]["safety"]["value"], 77)
        self.assertEqual(kw["base_results"], {f"{gate.RESULTS}abc/old.json": {"target": "chat-default", "config": "x"}})
        self.assertEqual(kw["base_served"], "config/models/base.yaml")
        self.assertIn(("diff", "--name-only", "--no-renames", "--diff-filter=DM", "BASE...HEAD"), calls)
        self.assertIn(("ls-tree", "-r", "--name-only", "BASE", "--", gate.RESULTS), calls)

    # Report only: a results file for a config the PR doesn't make served (a measurement) reports its scores
    # against the thresholds without failing the job.
    def test_a_measurement_below_threshold_is_reported_not_enforced(self):
        self.baseline()
        below = self.commit("below.json", date="2026-10-21T14:00:00Z")
        failed, text = self.run_gate([str(below)])
        self.assertFalse(failed)
        self.assertIn(f"### `chat-default` with `{CONFIG}` (report only): pass", text)
        self.assertIn("- Report only, not enforced: qa: judge_avg 3.50, gate ≥ baseline − 0.2: FAIL", text)
        self.assertIn("| safety | pass_pct | 100.00 | 50.00 | 50.00 | ≥ 100 | FAIL |", text)
        failed, text = self.run_gate(["README.md"])
        self.assertEqual((failed, "Nothing to check" in text), (False, True))

    # Report only still checks integrity, on the changed file itself (a fresh file beside it doesn't hide it).
    def test_a_measurement_must_pass_every_integrity_check(self):
        self.baseline()
        self.commit("good.json")
        for problem, path in (
                ("stale: config hash", self.commit("stale.json", fresh=False)),
                ("cases aren't", self.commit("good.json", date="2026-10-21T13:00:00Z",
                                             edit=lambda r: r.update(cases=r["cases"][:3]))),
                ("INVALID", self.commit("good.json", date="2026-10-21T14:00:00Z",
                                        edit=lambda r: r["cases"][0].update(status=500))),
                ("not projects/04", self.commit("good.json", date="2026-10-21T15:00:00Z",
                                                edit=lambda r: r.update(dataset="projects/04-other")))):
            failed, text = self.run_gate([str(path)])
            self.assertTrue(failed, problem)
            self.assertIn("(report only): FAIL", text)
            self.assertIn(problem, text)

    def test_every_config_in_the_comment_is_labelled(self):
        self.baseline()
        good, measurement = self.commit("good.json"), self.commit("below.json", date="2026-10-21T10:00:00Z")
        failed, text = self.run_gate([CONFIG, str(good), str(measurement)])
        self.assertFalse(failed)  # the served config's newest run passes; the older one is a report
        self.assertEqual(text.count("(enforced): pass"), 1)
        self.assertEqual(text.count("(report only): pass"), 1)  # good.json is the enforced one, not shown twice

    # Review item 4, and the comment step: results-only PRs run the gate; a read-only token can't turn it red.
    def test_evals_yml_triggers_on_results_and_the_comment_cant_fail_the_job(self):
        workflow = (run.ROOT / ".github/workflows/evals.yml").read_text()
        self.assertIn("      - projects/04-eval-gated-releases/results/**", workflow)
        self.assertRegex(workflow, r"gh pr comment [^\n]*\\\n\s+\|\| echo \"::warning::")

    def test_deleting_a_results_file_fails(self):
        gone = f"{self.results}/abc/old.json"
        failed, text = self.run_gate([gone], touched=[gone])
        self.assertTrue(failed)
        self.assertIn("append-only", text)


class GateFromBase(unittest.TestCase):
    """evals.yml's gate step, run as is (its `run:` block, in bash) on throwaway git repos. The base commit's gate
    code runs on the PR's tree (EVAL_ROOT). Nothing the PR commits can stand in for it, and the step proves which
    tree was evaluated."""

    EVALS = "projects/04-eval-gated-releases/evals"
    SHADOW = "import sys\nsys.exit(0)\n"  # imported or run, it would end the gate with a pass

    @staticmethod
    def step():
        """The gate step's `run:` block from evals.yml."""
        lines = (run.ROOT / ".github/workflows/evals.yml").read_text().splitlines()
        i = next(n for n, line in enumerate(lines) if "name: check the committed eval results with the base" in line)
        i = next(n for n in range(i, len(lines)) if lines[n].strip() == "run: |") + 1
        body = []
        while i < len(lines) and (not lines[i].strip() or lines[i].startswith(" " * 10)):
            body.append(lines[i][10:])
            i += 1
        return "\n".join(body)

    def repo(self, base_files, pr_files):
        """A git repo with a base commit and a PR commit on top: (its folder, the base sha)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = tmp.name

        def git(*args):
            return subprocess.run(["git", "-C", root, "-c", "user.name=test", "-c", "user.email=test@example.com",
                                   "-c", "commit.gpgsign=false", *args], check=True, capture_output=True,
                                  text=True).stdout
        git("init", "-q")
        shas = []
        for files in (base_files, pr_files):
            for path, text in files.items():
                (Path(root) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(root) / path).write_text(text)
            git("add", "-A")
            git("commit", "-q", "--allow-empty", "-m", "commit")
            shas.append(git("rev-parse", "HEAD").strip())
        return root, shas[0]

    def run_step(self, root, base):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = {**os.environ, "BASE": base, "EVALS": self.EVALS, "GITHUB_WORKSPACE": root, "RUNNER_TEMP": temp.name}
        env.pop("EVAL_ROOT", None)
        return subprocess.run(["bash", "-e", "-c", self.step()], cwd=root, env=env, text=True, capture_output=True,
                              check=False)

    def real_gate(self):
        """This branch's gate code, as a base commit would hold it, and the files it reads."""
        files = {f"{self.EVALS}/{name}": (HERE.parent / "evals" / name).read_text()
                 for name in ("gate.py", "run.py", "scorers.py", "judge.md", "thresholds.yaml")}
        return {**files, gate.SERVES: "config=${CONFIG:-config/models/a.yaml}\n", "config/models/a.yaml": "name: a\n"}

    # FIX 5: a PR that rewrites gate.py and plants modules to shadow the base gate's imports still fails.
    def test_a_pr_cant_replace_or_shadow_the_base_gate(self):
        root, base = self.repo(self.real_gate(), {
            gate.SERVES: "config=${CONFIG:-config/models/b.yaml}\n",  # chat-default serves a config with no results
            "config/models/b.yaml": "name: b\n",
            f"{self.EVALS}/gate.py": self.SHADOW,
            f"{self.EVALS}/json.py": self.SHADOW,
            f".gate-base/{self.EVALS}/json.py": self.SHADOW,
            f".gate-base/{self.EVALS}/argparse.py": self.SHADOW,
            f".gate-base/{self.EVALS}/gate.py": self.SHADOW})
        own = subprocess.run([sys.executable, f"{root}/{self.EVALS}/gate.py", base], capture_output=True, check=False)
        self.assertEqual(own.returncode, 0)  # what the PR's own gate would say
        step = self.run_step(root, base)
        self.assertEqual(step.returncode, 1, step.stdout + step.stderr)
        self.assertIn(f"gate-root: {Path(root).resolve()}\n", step.stdout)  # FIX 6: it evaluated the PR's tree
        self.assertIn("gate-changed: config/models/b.yaml\n", step.stdout)
        self.assertIn("`chat-default` with `config/models/b.yaml` (enforced): FAIL", step.stdout)

    # FIX 6: a base gate that evaluates any other tree fails the job.
    def test_a_gate_that_evaluated_another_tree_fails_the_job(self):
        root, base = self.repo({f"{self.EVALS}/run.py": "# reads EVAL_ROOT\n",
                                f"{self.EVALS}/gate.py": "print('gate-root: /somewhere/else')\n"}, {})
        step = self.run_step(root, base)
        self.assertEqual(step.returncode, 1, step.stdout + step.stderr)
        self.assertIn("::error::the gate didn't evaluate this PR's tree", step.stdout)

    # FIX 6's one exception: a base gate older than EVAL_ROOT (the PR that adds it) runs, and the job says it
    # proves nothing.
    def test_a_base_gate_older_than_eval_root_is_flagged_as_proving_nothing(self):
        root, base = self.repo({f"{self.EVALS}/run.py": "ROOT = 'its own checkout'\n",
                                f"{self.EVALS}/gate.py": "print('Nothing to check')\n"}, {})
        step = self.run_step(root, base)
        self.assertEqual(step.returncode, 0, step.stdout + step.stderr)
        self.assertIn("predates EVAL_ROOT", step.stdout)
        self.assertIn("this run proves nothing", step.stdout)

    def test_no_gate_in_the_base_commit_fails_closed_unless_the_pr_introduces_it(self):
        root, base = self.repo({"README.md": "x\n"}, {})
        step = self.run_step(root, base)
        self.assertEqual(step.returncode, 1)
        self.assertIn("doesn't introduce one", step.stdout)
        root, base = self.repo({"README.md": "x\n"}, {f"{self.EVALS}/run.py": "# EVAL_ROOT\n",
                                                      f"{self.EVALS}/gate.py": self.SHADOW})
        step = self.run_step(root, base)
        self.assertIn("this PR introduces it, so its own gate runs once", step.stdout)
        self.assertEqual(step.returncode, 1)  # its run still has to show it evaluated the PR's tree


if __name__ == "__main__":
    unittest.main()
