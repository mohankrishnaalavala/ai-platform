#!/usr/bin/env python3
"""P4-06: the eval gate that evals.yml runs on pull requests. It reads committed results files and never calls
a model: real runs happen in the session cluster window (run.py), and their results are committed.

Enforced: thresholds.yaml applies only to what the platform serves. That is chat-default's served config (the
default CONFIG in P1's deploy.sh), when the PR changes that config, which file it is, or the gate itself (evals/:
scorers, judge.md, dataset, thresholds; and the judge alias in helm/gateway-canary.yaml). Its newest results file in results/<its config hash in this commit>/
must exist (a missing one means the committed results are stale, or were never run), pass every integrity check
below and pass thresholds.yaml against the committed baseline. This keeps a bad candidate red.
Report only: every other results file the PR adds or changes (a measurement such as BF16 vs FP8, or a candidate
evaluated offline) must pass the integrity checks, but its scores against the thresholds are only reported.
Integrity: the config hash matches this commit; it ran on golden set v1 with the `judge` alias and holds every
case for every round; it is VALID. And committed results are append-only: a PR that modifies, moves or deletes
one fails (the diff runs without rename detection, so a move shows as a delete).

What the PR can't touch comes from the base commit: the thresholds (thresholds.yaml as it is there; a PR that
edits it gets a note, and the edit applies only after merge), which config main serves, and which results exist.

The baseline is never picked by the PR (run.py's --baseline field is ignored). It is the newest VALID, complete
results file for the same target, run on the baseline config as it is at the base commit, with this commit's
judge, scorers, dataset and judge model, other than the candidate. The baseline config is what main serves for
chat-default, and the config itself for any other target. It must already be in the base commit, except after a
gate change with the served config unchanged: then it is an earlier run of the same config (older than the
candidate by its recorded date), which may come in the same PR. So commit both runs of the noise band.
A gate change before the served config was ever evaluated (no results for it in the base commit) has nothing to
go stale, and passes with a note.
shortcut: a change to chat-hosted's model in the gateway values isn't detected, so a hosted candidate is report
only; enforce it once the canary overlay (P4-08) names the hosted model being served.

Usage: gate.py BASE_SHA [--out scores.md]. Prints the score table as markdown, each config labelled "enforced"
or "report only". Exit code 0 when every check passes, 1 otherwise. Standard library only.
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
THRESHOLDS = "projects/04-eval-gated-releases/evals/thresholds.yaml"
JUDGE_VALUES = "projects/04-eval-gated-releases/helm/gateway-canary.yaml"  # the judge alias's model: part of the gate
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


def enforced(changed, now_served, base_served):
    """The served config to enforce thresholds on, as {(target, config): why}: "served" or "gate"; or {}."""
    if now_served != base_served or now_served in changed:
        return {(SERVED_TARGET, now_served): "served"}
    if any(p.startswith(GATE_FILES) or p == JUDGE_VALUES for p in changed):
        return {(SERVED_TARGET, now_served): "gate"}
    return {}


def changed_results(changed):
    """The results files the PR adds or changes."""
    return [run.ROOT / p for p in changed if p.startswith(RESULTS) and p.endswith(".json") and (run.ROOT / p).exists()]


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
    except (FileNotFoundError, ValueError):  # no config, golden set v1 or judge model in this commit
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


def baseline_for(target, config, config_sha, exclude, accept):
    """The committed baseline (see the docstring at the top): (path, results) or None. accept(path, results) says
    which files may serve: those in the base commit, or for a gate change, any run older than the candidate."""
    try:
        parts = dict(run.hash_parts(config, DATASET, JUDGE), config=config_sha)
    except (FileNotFoundError, ValueError):
        return None
    for path in results_in(run.ROOT / RESULTS / run.config_hash(parts), target, config, exclude):
        r = load(path)
        if (r["hash_parts"]["config"] == config_sha and accept(path, r) and not run.invalid_reasons(r["cases"])
                and not coverage(r)):
            return path, r
    return None


def verify(path, thresholds, enforce, baseline):
    """The failures of one results file, and its score table. Integrity always counts; the thresholds (against
    baseline, (path, results) or None) count only when enforce, and are reported otherwise."""
    r = load(path)
    failures = coverage(r)
    try:
        fresh = run.config_hash(run.hash_parts(r["config"], r["dataset"], r["judge"])) == r["config_hash"]
    except (FileNotFoundError, ValueError):  # a missing file or judge model: not fresh
        fresh = False
    if not fresh:
        failures.append(f"stale: config hash {r['config_hash']} doesn't match the files in this commit")
    failures += [f"INVALID: {x}" for x in run.invalid_reasons(r["cases"])]
    rows, below = run.compare(r, baseline and baseline[1], thresholds, need_baseline=enforce)
    shown = dict(r, baseline=run.rel(baseline[0]) if baseline else None)  # the derived baseline, not the file's own
    if enforce:
        failures += below
        return failures, run.table(shown, rows, failures, "enforced")
    return failures, run.table(shown, rows, failures, "report only", below)


def gate(changed, touched, now_served, *, base_served, sha_at_base, base_results, thresholds):
    """(failed, markdown) for a pull request. changed: every path the PR adds, modifies or deletes; touched: the
    paths it modifies or deletes (both without rename detection, so a move is a delete and an add).
    Everything else comes from the base commit, never from the PR: base_served, sha_at_base(path) (a file's
    sha256 there, or None), base_results ({repo path: results} there) and thresholds (parsed, or None)."""
    report, failed, done = ["## Eval gate (P4)", ""], False, set()

    def baseline(target, config, path, why):
        base_config = base_served if target == SERVED_TARGET else config
        base_sha = sha_at_base(base_config) if base_config else None
        date = load(path)["date"]
        accept = ((lambda p, r: r["date"] < date) if why == "gate"  # the noise band: an earlier run, maybe this PR's
                  else (lambda p, r: run.rel(p) in base_results))  # otherwise only what main already has
        return baseline_for(target, base_config, base_sha, path, accept) if base_sha else None

    if THRESHOLDS in changed:
        report += [(f"**Note:** this PR changes `{THRESHOLDS}`. The gate uses the base commit's thresholds: the "
                    "change applies only after merge, and needs the project owner's approval (thresholds are "
                    "never edited to make a run pass)."), ""]
    rewritten = [p for p in touched if p.startswith(RESULTS)]
    if rewritten:
        failed = True
        report += [("**FAIL**: committed results are append-only; this PR modifies, moves or deletes "
                    f"{', '.join(rewritten)}"), ""]
    todo, extra = enforced(changed, now_served, base_served), changed_results(changed)
    if not (todo or extra or rewritten):
        report.append("Nothing to check: this PR changes neither the served config, the gate, nor a results file.")
    for (target, config), why in todo.items():
        path = newest(target, config)
        ever = any((r["target"], r["config"]) == (target, config) for r in base_results.values())
        if path is None and why == "gate" and not ever:
            report += [(f"`{target}` with `{config}` (enforced) was never evaluated, so a gate change leaves "
                        "nothing stale."), ""]
            continue
        if not thresholds:  # none, or empty, at the base commit: enforcing nothing would pass anything
            failed = True
            report += [f"### `{target}` with `{config}` (enforced): FAIL", "",
                       f"No thresholds in `{THRESHOLDS}` at the base commit, so there is nothing to enforce.", ""]
            continue
        if path is None:
            failed = True
            report += [f"### `{target}` with `{config}` (enforced): FAIL", "", ("No results file at its config hash "
                       "in this commit: the committed results are stale or missing. Run evals/run.py and commit "
                       "them."), ""]
            continue
        failures, md = verify(path, thresholds, True, baseline(target, config, path, why))
        failed, done = failed or bool(failures), done | {path.resolve()}
        report += [f"Results file: `{run.rel(path)}`", "", md]
    for path in extra:
        if path.resolve() in done:
            continue
        r = load(path)
        failures, md = verify(path, thresholds or {}, False, baseline(r["target"], r["config"], path, "results"))
        failed = failed or bool(failures)
        report += [f"Results file: `{run.rel(path)}`", "", md]
    return failed, "\n".join(report) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Check the committed eval results of a pull request (P4-06).")
    p.add_argument("base", help="the pull request's base commit")
    p.add_argument("--out", help="also write the markdown here")
    a = p.parse_args(argv)

    def at_base(path):
        """A file's bytes at the base commit, or None."""
        try:
            return git("show", f"{a.base}:{path}")
        except subprocess.CalledProcessError:
            return None

    def sha_at_base(path):
        blob = at_base(path)
        return None if blob is None else run.sha(blob)

    script, limits = at_base(SERVES), at_base(THRESHOLDS)
    try:
        base_served = None if script is None else served(script.decode())
    except ValueError:  # no default CONFIG at the base commit
        base_served = None
    listed = git("ls-tree", "-r", "--name-only", a.base, "--", RESULTS).decode().split()
    changed, touched = (git("diff", "--name-only", "--no-renames", *flag, f"{a.base}...HEAD").decode().split()
                        for flag in ([], ["--diff-filter=DM"]))
    # What was evaluated, for the log: evals.yml fails the job unless the root is the PR's tree.
    print(f"gate-root: {run.ROOT}")
    print("".join(f"gate-changed: {p}\n" for p in changed) or "gate-changed: (none)\n", end="")
    failed, text = gate(changed, touched, served((run.ROOT / SERVES).read_text()), base_served=base_served,
                        sha_at_base=sha_at_base,
                        base_results={p: json.loads(at_base(p)) for p in listed if p.endswith(".json")},
                        thresholds=None if limits is None else run.parse_thresholds(limits.decode()))
    print(text)
    if a.out:
        Path(a.out).write_text(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
