#!/usr/bin/env python3
"""Turn the benchmark result files (vllm bench serve --save-result) into summary.md.

Usage: summary.py <results dir> "<scenarios>" "<concurrencies>"
A run with failed requests is marked invalid (for example, the spot GPU was taken away mid-run), and
an expected run without a result file is listed as missing. Neither may be published.
"""
import glob
import json
import os
import sys

LENGTHS = {"chat": (512, 128), "rag": (4000, 256)}  # tokens in, tokens out; run.sh uses the same


def load(results_dir):
    runs = {}
    for path in glob.glob(os.path.join(results_dir, "*-c*.json")):
        with open(path) as f:
            r = json.load(f)
        runs[(r["scenario"], int(r["max_concurrency"]))] = r
    if not runs:
        sys.exit(f"no result files in {results_dir}")
    return runs


def summary(runs, scenarios, concurrencies):
    expected = [(s, c) for s in scenarios for c in concurrencies]
    missing = [k for k in expected if k not in runs]
    first = next(iter(runs.values()))
    lengths = "; ".join(f"{s} = {LENGTHS[s][0]} tokens in / {LENGTHS[s][1]} out" for s in scenarios)
    lines = ["# Benchmark summary" + (" (INCOMPLETE: some runs are missing)" if missing else ""), ""]
    lines += [
        f"- Config: `{first['config']}`, hash `{first['config_hash']}`, model revision `{first['revision']}`",
        f"- Session: {first['session']}, date: {first.get('date', '[X]')}",
        (f"- Hardware: {first['machine']}, 1x {first['gpu']}, spot: {first['spot']}; GPU zone "
         f"{first.get('gpu_zone', '[X]')}, client zone {first.get('client_zone', '[X]')}"),
        f"- Software: vLLM {first['vllm_version']} (image `{first['vllm_image']}`), GKE {first['gke']}",
        ("- Method: `vllm bench serve` as a Job inside the cluster; random prompts of fixed length "
         f"({lengths}); prompts = max(20, 10 x concurrency) after max(2, concurrency) warm-up requests; seed 0"),
        "",
        ("| Scenario | Concurrency | Requests ok / failed | TTFT p50 / p95 / p99 (ms) | TPOT p50 (ms) "
         "| Output tokens/s | Valid |"),
        "|---|---|---|---|---|---|---|",
    ]
    for s, c in expected:
        r = runs.get((s, c))
        if r is None:
            lines.append(f"| {s} | {c} | MISSING | | | | no: no result file |")
            continue
        valid = "yes" if r["failed"] == 0 else "no: failed requests (spot interruption?)"
        lines.append(
            f"| {s} | {c} | {r['completed']} / {r['failed']} "
            f"| {r['p50_ttft_ms']:.0f} / {r['p95_ttft_ms']:.0f} / {r['p99_ttft_ms']:.0f} "
            f"| {r['p50_tpot_ms']:.1f} | {r['output_throughput']:.1f} | {valid} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    print(summary(load(sys.argv[1]), sys.argv[2].split(), [int(c) for c in sys.argv[3].split()]), end="")
