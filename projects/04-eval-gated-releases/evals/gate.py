#!/usr/bin/env python3
"""P4-06: the eval gate that evals.yml runs on pull requests. It reads committed results files and never calls
a model: real runs happen in the session cluster window (run.py), and their results are committed.

The configs it checks, given the pull request's base commit:
- the config chat-default serves (the default CONFIG in P1's deploy.sh), when the PR changes that config or
  which file it is. Its results must have been compared with a baseline (run.py --baseline).
- the config of every results file the PR adds or changes. Without a baseline, only the absolute gates apply.
For each, the newest results file for that config in results/<its config hash in this commit>/, run on golden
set v1, must exist (a missing one means the committed results are stale, or were never run), be VALID and pass
thresholds.yaml. A PR that changes neither passes with nothing to check: a new, unserved model config, say.
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
RESULTS = "projects/04-eval-gated-releases/results/"
DATASET = "projects/04-eval-gated-releases/evals/datasets/v1"


def served(script):
    """The config chat-default serves: deploy.sh's default CONFIG."""
    found = re.search(r"^config=\$\{CONFIG:-([^}]+)\}", script, re.MULTILINE)
    if not found:
        raise ValueError(f"no default CONFIG in {SERVES}")
    return found.group(1)


def git(*args):
    return subprocess.run(["git", "-C", str(run.ROOT), *args], capture_output=True, text=True, check=True).stdout


def targets(changed, now_served, base_served):
    """The configs to check, each with whether its results need a baseline."""
    out = {}
    for path in changed:
        if path.startswith(RESULTS) and path.endswith(".json") and (run.ROOT / path).exists():
            out[json.loads((run.ROOT / path).read_text())["config"]] = False
    if now_served != base_served or now_served in changed:
        out[now_served] = True
    return out


def newest(config):
    """The newest results file for config at its config hash in this commit, or None."""
    try:
        folder = run.ROOT / RESULTS / run.config_hash(run.hash_parts(config, DATASET))
    except FileNotFoundError:  # the config, or golden set v1, isn't in this commit
        return None
    found = [(r["date"], p) for p in folder.glob("*.json") for r in [json.loads(p.read_text())] if r["config"] == config]
    return max(found)[1] if found else None


def verify(path, thresholds, need_baseline):
    """The failures of one results file, and its score table."""
    r = json.loads(Path(path).read_text())
    failures = []
    try:
        fresh = run.config_hash(run.hash_parts(r["config"], r["dataset"])) == r["config_hash"]
    except FileNotFoundError:
        fresh = False
    if not fresh:
        failures.append(f"stale: config hash {r['config_hash']} doesn't match the files in this commit")
    failures += [f"INVALID: {x}" for x in run.invalid_reasons(r["cases"])]
    baseline = None
    if r.get("baseline"):
        try:
            baseline = json.loads((run.ROOT / r["baseline"]).read_text())
        except FileNotFoundError:
            failures.append(f"the baseline {r['baseline']} isn't in this commit")
    rows, gate_failures = run.compare(r, baseline, thresholds, need_baseline)
    failures += gate_failures
    return failures, run.table(r, rows, failures)


def main(argv=None):
    p = argparse.ArgumentParser(description="Check the committed eval results of a pull request (P4-06).")
    p.add_argument("base", help="the pull request's base commit")
    p.add_argument("--out", help="also write the markdown here")
    a = p.parse_args(argv)
    changed = git("diff", "--name-only", f"{a.base}...HEAD").split()
    try:
        base_served = served(git("show", f"{a.base}:{SERVES}"))
    except (subprocess.CalledProcessError, ValueError):
        base_served = None
    todo = targets(changed, served((run.ROOT / SERVES).read_text()), base_served)
    thresholds, report, failed = run.load_thresholds(), ["## Eval gate (P4)", ""], False
    if not todo:
        report.append("Nothing to check: this PR changes neither the config chat-default serves nor a results file.")
    for config, need_baseline in sorted(todo.items()):
        path = newest(config)
        if path is None:
            failed = True
            report += [f"### `{config}`: FAIL", "", ("No results file at its config hash in this commit: the committed "
                       "results are stale or missing. Run evals/run.py in a session and commit the results."), ""]
            continue
        failures, md = verify(path, thresholds, need_baseline)
        failed = failed or bool(failures)
        report += [f"Results file: `{run.rel(path)}`", "", md]
    text = "\n".join(report) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
