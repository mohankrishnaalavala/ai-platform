#!/usr/bin/env python3
"""P4-05: the eval runner. It sends every case of a dataset to one gateway alias, scores the answers
(scorers.py, plus the judge for Q&A) and writes the results as JSON and as a markdown table, in
results/<config hash>/<UTC time>-<target>.json and .md.

Usage, in the session cluster window, through a port-forward to the gateway:
  GATEWAY_URL=http://127.0.0.1:4000 MASTER_KEY=... evals/run.py --target chat-default \\
      --config config/models/qwen2.5-7b-bf16.yaml [--baseline <results file>] [--repeat 2]
--config is the target's config file: the vLLM config behind chat-default, or the gateway values for a hosted alias.

Config hash: the first 16 hex characters of a sha256 over five parts: the sha256 of the target's config file,
judge.md, scorers.py and the dataset's files, and the judge alias's model in P2's gateway values. Changing any of
them makes old results stale, and evals.yml (gate.py) fails a pull request whose results don't match its files.

INVALID: a run is INVALID when any call to the target or the judge gave no HTTP 200 answer or was answered by a
fallback, or when the judge gave no score. Fallback detection relies on LiteLLM's response header
x-litellm-attempted-fallbacks: "0" means the alias's own model answered; anything else, or no header, counts as
a fallback. P2 saw "1" on every fallback and "0" otherwise, in CI and in its session 4. Every call also records
x-litellm-model-name, the model that answered (the answer body names only the alias).

Cost (P4-11): tokens x price for every call, priced by the answering model in evals/prices.json, plus the run's
minutes x the on-demand GPU node price in P1's prices.json when the target is self-hosted (its config has a
`gpu:` key; its own tokens are then paid for by those minutes). An unknown price makes that part and the total
null, shown as [X].

--repeat 2 runs every case twice; the table shows each round, which is the noise band.
Exit code: 0 for a VALID run, 1 for INVALID. The files are written either way. Standard library only.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import scorers

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parents[2]
THRESHOLDS = EVALS / "thresholds.yaml"
PRICES = EVALS / "prices.json"
GPU_PRICES = ROOT / "projects/01-gpu-serving/bench/prices.json"
GATEWAY_VALUES = ROOT / "projects/02-llm-gateway/helm/gateway/values.yaml"
TIMEOUT_S = 180  # longer than the gateway's own timeouts (30 s for chat-default, 60 s for chat-hosted)
FALLBACK_RULE = ("answered by a fallback when LiteLLM's x-litellm-attempted-fallbacks response header is not 0; "
                 "a missing header counts as a fallback")
GATES = {"at_least": "≥ {:g}", "baseline_minus": "≥ baseline − {:g}"}
CANARY_GATES = {"baseline_times_at_most"}  # the canary's latency gate, checked by promote.py (P4-08)


def rel(path):
    """A path as results files record it: relative to the repo root, with forward slashes."""
    return Path(os.path.relpath(Path(path).resolve(), ROOT)).as_posix()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def alias_model(alias):
    """The model behind a gateway alias in P2's values (the first `model:` after its `model_name`), or None.
    shortcut: a regex on that file's layout, not a YAML parser; use one if the layout changes."""
    found = re.search(rf"^\s*- model_name: {re.escape(alias)}(?![\w-]).*\n(?:.*\n)*?\s*model:\s*(\S+)",
                      GATEWAY_VALUES.read_text(), re.MULTILINE)
    return found.group(1) if found else None


def hash_parts(config, dataset, judge):
    """Each part the config hash covers. config and dataset are relative to the repo root; judge is an alias."""
    files = sorted((ROOT / dataset).glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"no *.jsonl files in {dataset}")
    return {"config": sha((ROOT / config).read_bytes()), "judge_md": sha(scorers.JUDGE_MD.read_bytes()),
            "scorers": sha(Path(scorers.__file__).read_bytes()),
            "dataset": sha(b"".join(f.name.encode() + b"\0" + f.read_bytes() for f in files)),
            "judge_model": alias_model(judge)}


def config_hash(parts):
    return sha(" ".join(str(parts[k]) for k in sorted(parts)).encode())[:16]


def load_cases(dataset):
    return [json.loads(line) for f in sorted((ROOT / dataset).glob("*.jsonl"))
            for line in f.read_text().splitlines() if line.strip()]


def post(alias, messages, settings):
    """One chat call through the gateway. Error bodies are dropped: LiteLLM's can quote part of a key."""
    req = urllib.request.Request(
        os.environ["GATEWAY_URL"].rstrip("/") + "/v1/chat/completions", method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['MASTER_KEY']}"},
        data=json.dumps({"model": alias, "messages": messages, **settings}).encode())
    out, headers, start = {"status": None, "text": None, "tokens": None}, {}, time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            headers, status, body = r.headers, r.status, json.loads(r.read())
        usage = body.get("usage") or {}
        out.update(status=status, text=body["choices"][0]["message"]["content"] or "",
                   tokens={"prompt": usage.get("prompt_tokens"), "completion": usage.get("completion_tokens")})
    except urllib.error.HTTPError as e:
        headers, out["status"] = e.headers, e.code
    except (OSError, ValueError, LookupError, TypeError):
        pass  # no usable answer (no connection, a timeout, or a body that isn't a chat answer): status stays None
    out.update(model=headers.get("x-litellm-model-name"), fallbacks=headers.get("x-litellm-attempted-fallbacks"),
               seconds=round(time.monotonic() - start, 3))
    return out


def run_case(case, target, judge, round_):
    """One case: the target's answer, then its score (a check, or the judge for Q&A)."""
    system = [{"role": "system", "content": case["system"]}] if case.get("system") else []
    out = post(target, system + [{"role": "user", "content": case["prompt"]}], scorers.TARGET_REQUEST)
    kind = case["check"]["type"]
    row = {"id": case["id"], "category": case["category"], "round": round_, "kind": kind, "passed": None,
           "score": None, "answer": out.pop("text"), **out}
    if row["answer"] is not None and kind == "judge":
        reply = post(judge, [{"role": "user", "content": scorers.judge_prompt(case, row["answer"])}],
                     scorers.JUDGE_REQUEST)
        reply["reply"] = reply.pop("text")
        row.update(judge=reply, score=scorers.judge_score(reply["reply"]))
    elif row["answer"] is not None:
        row["passed"] = scorers.check(row["answer"], case["check"])
    return row


def invalid_reasons(cases):
    """Why a run is INVALID: a call without an answer, an answer from a fallback, or a Q&A case without a score."""
    reasons = []
    for c in cases:
        where, before = f"{c['id']} (round {c['round']})", len(reasons)
        for who, call in [("target", c)] + ([("judge", c["judge"])] if c.get("judge") else []):
            if call["status"] != 200:
                reasons.append(f"{where}: the {who} gave no answer (HTTP status {call['status']})")
            elif call["fallbacks"] != "0":
                reasons.append(f"{where}: the {who} was answered by a fallback ({call['model']}, "
                               f"x-litellm-attempted-fallbacks {call['fallbacks']})")
        if c["kind"] == "judge" and c["score"] is None and len(reasons) == before:
            reasons.append(f"{where}: the judge gave no score")
    return reasons


def metrics(cases):
    passed = [c["passed"] for c in cases if c["passed"] is not None]
    scores = [c["score"] for c in cases if c["score"] is not None]
    return {**({"pass_pct": 100 * sum(passed) / len(passed)} if passed else {}),
            **({"judge_avg": sum(scores) / len(scores)} if scores else {})}


def summarize(cases):
    """Per category: each metric's mean over the rounds, and each round's own value (the noise band)."""
    summary = {}
    for cat in sorted({c["category"] for c in cases}):
        rounds = [metrics([c for c in cases if c["category"] == cat and c["round"] == r])
                  for r in sorted({c["round"] for c in cases})]
        names = sorted({m for r in rounds for m in r})
        summary[cat] = {m: sum(r[m] for r in rounds if m in r) / sum(m in r for r in rounds) for m in names}
        summary[cat]["rounds"] = rounds
    return summary


def load_thresholds(path=THRESHOLDS):
    return parse_thresholds(Path(path).read_text())


def parse_thresholds(text):
    """thresholds.yaml's two levels: a category, then its metric, gate and value."""
    out = {}
    for line in text.splitlines():
        text = line.split("#")[0].rstrip()
        if text:
            key, _, value = text.strip().partition(":")
            if not text.startswith(" "):
                current = out[key] = {}
            else:
                current[key] = float(value) if key == "value" else value.strip()
    return out


def compare(results, baseline, thresholds, need_baseline=True):
    """One row per gate in thresholds.yaml, and the failures. Without a baseline, a gate relative to it fails
    when need_baseline, and is only shown otherwise."""
    now, was = summarize(results["cases"]), summarize(baseline["cases"]) if baseline else {}
    failures = []
    if baseline and any(baseline["hash_parts"].get(k) != v for k, v in results["hash_parts"].items() if k != "config"):
        failures.append("the baseline ran with a different judge.md, scorers.py, dataset or judge model")
    rows = []
    for cat, t in thresholds.items():
        if t["gate"] in CANARY_GATES:
            continue
        if t["gate"] not in GATES:
            raise ValueError(f"unknown gate in thresholds.yaml: {t['gate']}")
        have, base = now.get(cat, {}).get(t["metric"]), was.get(cat, {}).get(t["metric"])
        limit = t["value"] if t["gate"] == "at_least" else None if base is None else base - t["value"]
        if have is None:
            verdict = "FAIL: no cases"
        elif limit is None:
            verdict = "FAIL: no baseline" if need_baseline else "no baseline"
        else:
            verdict = "pass" if round(have - limit, 9) >= 0 else "FAIL"
        if verdict.startswith("FAIL"):
            failures.append(f"{cat}: {t['metric']} {fmt(have)}, gate {GATES[t['gate']].format(t['value'])}: {verdict}")
        rows.append({"category": cat, "metric": t["metric"], "baseline": base, "value": have,
                     "rounds": [r.get(t["metric"]) for r in now.get(cat, {}).get("rounds", [])],
                     "gate": GATES[t["gate"]].format(t["value"]), "verdict": verdict})
    return rows, failures


def cost(cases, self_hosted, minutes):
    """P4-11: tokens x price for every call, plus the GPU node's minutes when the target is self-hosted."""
    prices = json.loads(PRICES.read_text())["tokens"]
    tokens = {who: {"prompt": 0, "completion": 0} for who in ("target", "judge")}
    usd, unpriced = 0.0, set()
    for c in cases:
        for who, call in (("target", c), ("judge", c.get("judge"))):
            if not call or not call["tokens"]:
                continue
            used = {k: call["tokens"][k] or 0 for k in ("prompt", "completion")}
            for k, n in used.items():
                tokens[who][k] += n
            if who == "target" and self_hosted:
                continue  # paid for by the GPU minutes
            price = prices.get(call["model"]) or {}
            if price.get("input_per_1m") is None or price.get("output_per_1m") is None:
                unpriced.add(str(call["model"]))
            else:
                usd += (used["prompt"] * price["input_per_1m"] + used["completion"] * price["output_per_1m"]) / 1e6
    gpu = None
    if self_hosted:
        per_hour = json.loads(GPU_PRICES.read_text())["gpu_node"]["on_demand_per_hour"]
        gpu = {"minutes": round(minutes, 4), "usd_per_hour": per_hour, "usd": minutes / 60 * per_hour,
               "price": "on-demand L4 node, projects/01-gpu-serving/bench/prices.json"}
    token_usd = None if unpriced else usd
    return {"tokens": tokens, "token_usd": token_usd, "unpriced_models": sorted(unpriced), "gpu": gpu,
            "total_usd": None if token_usd is None else token_usd + (gpu["usd"] if gpu else 0)}


def fmt(x):
    return "–" if x is None else f"{x:.2f}"


def usd(x):
    return "[X]" if x is None else f"${x:.4f}"


def table(results, rows, problems, label=None, notes=()):
    """The markdown table: run facts, problems, then one row per gate. label: e.g. "enforced" or "report only";
    notes: findings shown without failing (a report-only file's threshold misses)."""
    r, c = results, results["cost"]
    models = Counter(str(case["model"]) for case in r["cases"])
    judges = Counter(str(case["judge"]["model"]) for case in r["cases"] if case.get("judge"))
    spend = f"{usd(c['total_usd'])} in all; tokens {usd(c['token_usd'])}"
    if c["unpriced_models"]:
        spend += f" (no price yet for {', '.join(c['unpriced_models'])})"
    if c["gpu"]:
        spend += f"; GPU {c['gpu']['minutes']:.2f} min at ${c['gpu']['usd_per_hour']:.4f}/h = {usd(c['gpu']['usd'])}"
    lines = [f"### `{r['target']}` with `{r['config']}`{f' ({label})' if label else ''}: {'FAIL' if problems else 'pass'}", "",
             f"- Run: {r['status']}, {r['rounds']} round(s), {r['date']}, commit `{r['git_commit']}`",
             f"- Config hash `{r['config_hash']}`; dataset {r['dataset_version']}; judge alias `{r['judge']}`",
             "- Answered by: " + ", ".join(f"`{m}` ({n})" for m, n in models.items()),
             "- Judged by: " + (", ".join(f"`{m}` ({n})" for m, n in judges.items()) or "no judge calls"),
             f"- Baseline: `{r['baseline']}`" if r["baseline"] else "- Baseline: none",
             f"- Cost: {spend}"]
    lines += [f"- Problem: {p}" for p in problems[:10]]
    if len(problems) > 10:
        lines.append(f"- … and {len(problems) - 10} more problems (see the results file)")
    lines += [f"- Report only, not enforced: {n}" for n in notes]
    lines += ["", "| Category | Metric | Baseline | This run | Each round | Gate | Result |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {x['category']} | {x['metric']} | {fmt(x['baseline'])} | {fmt(x['value'])} | "
              f"{' / '.join(fmt(v) for v in x['rounds'])} | {x['gate']} | {x['verdict']} |" for x in rows]
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Run the evals against one gateway alias (P4-05).")
    p.add_argument("--target", required=True, help="the gateway alias to evaluate, e.g. chat-default")
    p.add_argument("--config", required=True, help="the target's config file (part of the config hash)")
    p.add_argument("--baseline", help="a results file to compare with")
    p.add_argument("--dataset", default=str(EVALS / "datasets" / "v1"))
    p.add_argument("--judge", default="judge", help="the judge's gateway alias")
    p.add_argument("--repeat", type=int, default=1, help="rounds; 2 shows the noise band")
    p.add_argument("--out", default=str(EVALS.parent / "results"))
    a = p.parse_args(argv)
    if not (os.environ.get("GATEWAY_URL") and os.environ.get("MASTER_KEY")):
        sys.exit("set GATEWAY_URL and MASTER_KEY")
    config, dataset = rel(a.config), rel(a.dataset)
    parts, cases = hash_parts(config, dataset, a.judge), load_cases(dataset)
    for case in cases:  # before any call, so a dataset error never costs GPU minutes
        try:
            if case["check"]["type"] != "judge":
                scorers.validate(case["check"])
        except ValueError as e:
            sys.exit(f"{case['id']}: {e}")
    baseline = json.loads(Path(a.baseline).read_text()) if a.baseline else None

    start, rows = time.monotonic(), []
    for round_ in range(1, a.repeat + 1):
        for case in cases:
            rows.append(run_case(case, a.target, a.judge, round_))
            x = rows[-1]
            print(f"{x['id']} round {round_}: HTTP {x['status']}, {x['model']}, fallbacks {x['fallbacks']}, "
                  f"passed {x['passed']}, score {x['score']}, {x['seconds']} s", file=sys.stderr)
    minutes = (time.monotonic() - start) / 60

    now, reasons = datetime.now(timezone.utc), invalid_reasons(rows)
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
                            capture_output=True, text=True, check=False).stdout.strip()  # None outside a git checkout
    self_hosted = re.search(r"^gpu:", (ROOT / config).read_text(), re.MULTILINE) is not None
    results = {"status": "INVALID" if reasons else "VALID", "invalid_reasons": reasons, "target": a.target,
               "config": config, "dataset": dataset, "dataset_version": Path(dataset).name, "judge": a.judge,
               "rounds": a.repeat, "config_hash": config_hash(parts), "hash_parts": parts,
               "baseline": rel(a.baseline) if a.baseline else None, "date": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "git_commit": commit or None, "python": platform.python_version(), "fallback_rule": FALLBACK_RULE,
               "cost": cost(rows, self_hosted, minutes), "summary": summarize(rows), "cases": rows}
    gate_rows, failures = compare(results, baseline, load_thresholds(), need_baseline=False)
    md = table(results, gate_rows, reasons + failures)
    stem = str(Path(a.out) / results["config_hash"] / f"{now:%Y%m%dt%H%M%Sz}-{a.target}")  # an alias may hold a dot
    Path(stem).parent.mkdir(parents=True, exist_ok=True)
    Path(stem + ".json").write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n")
    Path(stem + ".md").write_text(md)
    print(md + f"\nwrote {stem}.json and .md")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
