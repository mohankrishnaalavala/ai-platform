#!/usr/bin/env python3
"""P4-06: the eval gate that evals.yml runs on pull requests. It reads committed results files and never calls
a model: real runs happen in the session cluster window (run.py), and their results are committed.

What it checks, given the pull request's base commit:
- chat-default's served config (the default CONFIG in P1's deploy.sh), with a baseline required, when the PR
  changes that config, which file it is, or the gate itself (evals/: scorers, judge.md, dataset, thresholds).
- the target and config of every results file the PR adds or changes. Without a baseline, only the absolute
  gates apply.
- no results file is deleted: results are history.
For each target and config, the newest results file in results/<its config hash in this commit>/ must exist (a
missing one means the committed results are stale, or were never run), cover golden set v1 with the `judge`
alias (every case, every round), be VALID and pass thresholds.yaml against the committed baseline.

The baseline is never picked by the PR (run.py's --baseline field is ignored). It is the newest VALID, complete
results file for the same target, run on the baseline config as it is at the base commit, with this commit's
judge, scorers, dataset and judge model, other than the candidate. The baseline config is what main serves for
chat-default, and the config itself for any other target. So when the served config doesn't change (a gate
change), its baseline is an earlier run of the same config: commit both runs of the noise band.
A gate change before the served config was ever evaluated has nothing to go stale, and passes with a note.
shortcut: a change to chat-hosted's model in the gateway values isn't detected, so a hosted candidate's PR must
add its results file; wire hosted candidates in with the canary overlay (P4-08).

Usage: gate.py BASE_SHA [--out scores.md]. Prints the score table as markdown. Exit code 0 when every check
passes, 1 otherwise. Standard library only.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import run

SERVES = "projects/01-gpu-serving/deploy.sh"
SERVED_TARGET = "chat-default"
GATE_FILES = "projects/04-eval-gated-releases/evals/"
RESULTS = "projects/04-eval-gated-releases/results/"
DATASET = "projects/04-eval-gated-releases/evals/datasets/v1"
JUDGE = "judge"


def served(script):
    """The config chat-default serves: deploy.sh's default CONFIG."""
    found = re.search(r"^config=\$\{CONFIG:-([^}]+)\}", script, re.MULTILINE)
    if not found:
        raise ValueError(f"no default CONFIG in {SERVES}")
    return found.group(1)


def git(*args):
    return subprocess.run(["git", "-C", str(run.ROOT), *args], capture_output=True, check=True).stdout


def load(path):
    return json.loads(Path(path).read_text())


def targets(changed, now_served, base_served):
    """{(target, config): why}: "served" or "gate" (a baseline is required), or "results" (it isn't)."""
    out = {}
    for path in changed:
        if path.startswith(RESULTS) and path.endswith(".json") and (run.ROOT / path).exists():
            r = load(run.ROOT / path)
            out[(r["target"], r["config"])] = "results"
    if now_served != base_served or now_served in changed:
        out[(SERVED_TARGET, now_served)] = "served"
    elif any(p.startswith(GATE_FILES) for p in changed):
        out[(SERVED_TARGET, now_served)] = "gate"
    return out


def results_in(folder, target, config, exclude=None):
    """The results files in folder for target and config, newest first, never exclude."""
    exclude = exclude and Path(exclude).resolve()
    found = [(r["date"], p) for p in Path(folder).glob("*.json") if p.resolve() != exclude
             for r in [load(p)] if (r["target"], r["config"]) == (target, config)]
    return [p for _, p in sorted(found, reverse=True)]


def newest(target, config):
    """The newest results file for target and config at its config hash in this commit, or None."""
    try:
        folder = run.ROOT / RESULTS / run.config_hash(run.hash_parts(config, DATASET, JUDGE))
    except FileNotFoundError:  # the config, or golden set v1, isn't in this commit
        return None
    return next(iter(results_in(folder, target, config)), None)


def coverage(r):
    """Why results don't cover DATASET: another dataset or judge, or cases missing, extra, doubled or relabelled."""
    if r["dataset"] != DATASET:
        return [f"ran on {r['dataset']}, not {DATASET}"]
    problems = [] if r["judge"] == JUDGE else [f"judged by the alias {r['judge']}, not {JUDGE}"]
    want = sorted((c["id"], c["category"], n) for c in run.load_cases(DATASET) for n in range(1, r["rounds"] + 1))
    if r["rounds"] < 1 or sorted((c["id"], c["category"], c["round"]) for c in r["cases"]) != want:
        problems.append(f"its cases aren't the dataset's cases × {r['rounds']} round(s)")
    return problems


def baseline_for(target, config, config_sha, exclude):
    """The committed baseline (see the docstring at the top): (path, results) or None."""
    try:
        parts = dict(run.hash_parts(config, DATASET, JUDGE), config=config_sha)
    except FileNotFoundError:
        return None
    for path in results_in(run.ROOT / RESULTS / run.config_hash(parts), target, config, exclude):
        r = load(path)
        if r["hash_parts"]["config"] == config_sha and not run.invalid_reasons(r["cases"]) and not coverage(r):
            return path, r
    return None


def verify(path, thresholds, need_baseline, baseline):
    """The failures of one results file against its baseline ((path, results) or None), and its score table."""
    r = load(path)
    failures = coverage(r)
    try:
        fresh = run.config_hash(run.hash_parts(r["config"], r["dataset"], r["judge"])) == r["config_hash"]
    except FileNotFoundError:
        fresh = False
    if not fresh:
        failures.append(f"stale: config hash {r['config_hash']} doesn't match the files in this commit")
    failures += [f"INVALID: {x}" for x in run.invalid_reasons(r["cases"])]
    rows, gate_failures = run.compare(r, baseline and baseline[1], thresholds, need_baseline)
    failures += gate_failures
    shown = dict(r, baseline=run.rel(baseline[0]) if baseline else None)  # the derived baseline, not the file's own
    return failures, run.table(shown, rows, failures)


def gate(changed, deleted, now_served, base_served, sha_at_base, thresholds):
    """(failed, markdown) for a pull request. sha_at_base(path) is the file's sha256 at the base commit, or None."""
    report, failed = ["## Eval gate (P4)", ""], False
    gone = [p for p in deleted if p.startswith(RESULTS)]
    if gone:
        failed = True
        report += [f"**FAIL**: results files are history and must not be deleted: {', '.join(gone)}", ""]
    todo = targets(changed, now_served, base_served)
    if not todo and not gone:
        report.append("Nothing to check: this PR changes neither the served config, the gate, nor a results file.")
    for (target, config), why in sorted(todo.items()):
        path = newest(target, config)
        ever = any((r["target"], r["config"]) == (target, config)
                   for r in map(load, (run.ROOT / RESULTS).glob("*/*.json")))
        if path is None and why == "gate" and not ever:
            report += [f"`{target}` with `{config}` was never evaluated, so a gate change leaves nothing stale.", ""]
            continue
        if path is None:
            failed = True
            report += [f"### `{target}` with `{config}`: FAIL", "", ("No results file at its config hash in this "
                       "commit: the committed results are stale or missing. Run evals/run.py and commit them."), ""]
            continue
        base_config = base_served if target == SERVED_TARGET else config
        base_sha = sha_at_base(base_config) if base_config else None
        baseline = baseline_for(target, base_config, base_sha, path) if base_sha else None
        failures, md = verify(path, thresholds, why != "results", baseline)
        failed = failed or bool(failures)
        report += [f"Results file: `{run.rel(path)}`", "", md]
    return failed, "\n".join(report) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Check the committed eval results of a pull request (P4-06).")
    p.add_argument("base", help="the pull request's base commit")
    p.add_argument("--out", help="also write the markdown here")
    a = p.parse_args(argv)

    def sha_at_base(path):
        try:
            return run.sha(git("show", f"{a.base}:{path}"))
        except subprocess.CalledProcessError:  # not in the base commit
            return None

    try:
        base_served = served(git("show", f"{a.base}:{SERVES}").decode())
    except (subprocess.CalledProcessError, ValueError):  # no deploy.sh, or no default CONFIG, at the base commit
        base_served = None
    changed, deleted = (git("diff", "--name-only", *flag, f"{a.base}...HEAD").decode().split()
                        for flag in ([], ["--diff-filter=D"]))
    failed, text = gate(changed, deleted, served((run.ROOT / SERVES).read_text()), base_served, sha_at_base,
                        run.load_thresholds())
    print(text)
    if a.out:
        Path(a.out).write_text(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
