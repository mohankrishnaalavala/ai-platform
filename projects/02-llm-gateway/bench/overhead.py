#!/usr/bin/env python3
"""What the gateway adds to latency: compare the same load run straight to vLLM and through the gateway.

Usage: overhead.py <direct results dir> <gateway results dir>
Each dir holds `vllm bench serve` results named <scenario>-c<concurrency>.json (overhead.sh makes them).
Prints a markdown table: TTFT (time to first token) and end-to-end latency, p50 and p95, per
concurrency, and the difference. Refuses runs with failed requests or different configs; flags runs
where the gateway changed the output length, because then their end-to-end times aren't comparable.
"""
import glob
import json
import os
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


def compare(direct, gateway):
    if set(direct) != set(gateway):
        sys.exit(f"the two runs cover different loads: {sorted(direct)} vs {sorted(gateway)}")
    lines = ["| Scenario | Concurrency | Measure | Straight to vLLM (ms) | Through the gateway (ms) | Added (ms) |",
             "|---|---|---|---|---|---|"]
    notes = []
    for key in sorted(direct):
        d, g = direct[key], gateway[key]
        where = f"{key[0]} c{key[1]}"
        for r in (d, g):
            if r.get("failed", 1) != 0:
                sys.exit(f"{where}: failed requests in a run; not valid")
        if d.get("config_hash") != g.get("config_hash"):
            sys.exit(f"{where}: the runs used different configs ({d.get('config_hash')} vs {g.get('config_hash')})")
        same_length = d.get("total_output_tokens") == g.get("total_output_tokens")
        if not same_length:
            notes.append(f"- {where}: output tokens differ (straight {d.get('total_output_tokens')}, "
                         f"gateway {g.get('total_output_tokens')}), so end-to-end times aren't comparable.")
        for label, field in METRICS:
            if "e2el" in field and not same_length:
                added = "[X]"
            else:
                added = f"{g[field] - d[field]:+.0f}"
            lines.append(f"| {key[0]} | {key[1]} | {label} | {d[field]:.0f} | {g[field]:.0f} | {added} |")
    return "\n".join(lines + ([""] + notes if notes else [])) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(compare(load(sys.argv[1]), load(sys.argv[2])), end="")
