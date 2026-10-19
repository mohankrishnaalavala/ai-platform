#!/usr/bin/env python3
"""What the gateway adds to latency: compare the same load run straight to vLLM and through the gateway.

Usage: overhead.py <round dir>...
Each round dir holds direct/<time>/ and gateway/<time>/ with `vllm bench serve` results named
<scenario>-c<concurrency>.json (overhead.sh makes one round dir per round; session 3 had one round, in
the session dir itself). Prints a markdown table per round: TTFT (time to first token) and end-to-end
latency, p50 and p95, per concurrency, and the difference; then, with more than one round, the median
of what the gateway added. Refuses runs with failed requests or different configs, and gateway runs
that sent each answer in one piece; flags runs where the gateway changed the output length, because
then their end-to-end times aren't comparable.
"""
import glob
import json
import os
import statistics
import sys

METRICS = [("TTFT p50", "p50_ttft_ms"), ("TTFT p95", "p95_ttft_ms"), ("End to end p50", "p50_e2el_ms"),
           ("End to end p95", "p95_e2el_ms")]


def load(d):
    runs = {}
    for path in sorted(glob.glob(os.path.join(d, "*-c*.json"))):
        with open(path) as f:
            r = json.load(f)
        runs[(r["scenario"], r["max_concurrency"])] = r
    if not runs:
        sys.exit(f"no results in {d}")
    return runs


def half(round_dir, name):
    dirs = glob.glob(os.path.join(round_dir, name, "*", ""))
    if len(dirs) != 1:
        sys.exit(f"{round_dir}: expected one {name} run, found {len(dirs)}")
    return load(dirs[0])


def added(direct, gateway):
    """Per (scenario, concurrency, measure): (straight, through the gateway, added or None when not
    comparable); and notes."""
    if set(direct) != set(gateway):
        sys.exit(f"the two runs cover different loads: {sorted(direct)} vs {sorted(gateway)}")
    rows, notes = {}, []
    for key in sorted(direct):
        d, g = direct[key], gateway[key]
        where = f"{key[0]} c{key[1]}"
        for r in (d, g):
            if r.get("failed", 1) != 0:
                sys.exit(f"{where}: failed requests in a run; not valid")
        if d.get("config_hash") != g.get("config_hash"):
            sys.exit(f"{where}: the runs used different configs ({d.get('config_hash')} vs {g.get('config_hash')})")
        if g.get("p50_itl_ms", 0) == 0:  # session 3: TTFT was the whole answer, so it measured no overhead
            sys.exit(f"{where}: the gateway sent each answer in one piece (inter-token latency 0); not valid")
        same_length = d.get("total_output_tokens") == g.get("total_output_tokens")
        if not same_length:
            notes.append(f"- {where}: output tokens differ (straight {d.get('total_output_tokens')}, "
                         f"gateway {g.get('total_output_tokens')}), so end-to-end times aren't comparable.")
        for label, field in METRICS:
            comparable = same_length or "e2el" not in field
            rows[(*key, label)] = (d[field], g[field], g[field] - d[field] if comparable else None)
    return rows, notes


def ms(value):
    return "[X]" if value is None else f"{value:+.0f}"


def compare(direct, gateway):
    """One round as a markdown table."""
    rows, notes = added(direct, gateway)
    lines = ["| Scenario | Concurrency | Measure | Straight to vLLM (ms) | Through the gateway (ms) | Added (ms) |",
             "|---|---|---|---|---|---|"]
    for (scenario, c, label), (d, g, a) in rows.items():
        lines.append(f"| {scenario} | {c} | {label} | {d:.0f} | {g:.0f} | {ms(a)} |")
    return "\n".join(lines + ([""] + notes if notes else [])) + "\n"


def medians(rounds):
    """What the gateway added in each round, and the median; [X] when a round isn't comparable."""
    if any(set(r) != set(rounds[0]) for r in rounds):
        sys.exit("the rounds cover different loads")
    lines = ["| Scenario | Concurrency | Measure | Added per round (ms) | Median added (ms) |", "|---|---|---|---|---|"]
    for key in rounds[0]:
        each = [r[key][2] for r in rounds]
        median = None if None in each else statistics.median(each)
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {', '.join(ms(a) for a in each)} | {ms(median)} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rounds, configs = [], set()
    for i, d in enumerate(sys.argv[1:], 1):
        direct, gateway = half(d, "direct"), half(d, "gateway")
        configs |= {r.get("config_hash") for r in direct.values()}
        print(f"## Round {i} ({d})\n\n{compare(direct, gateway)}")
        rounds.append(added(direct, gateway)[0])
    if len(configs) != 1:
        sys.exit(f"the rounds used different configs: {sorted(map(str, configs))}")
    if len(rounds) > 1:
        print(f"## Median of {len(rounds)} rounds\n\n{medians(rounds)}", end="")
