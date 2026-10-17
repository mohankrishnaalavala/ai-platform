#!/usr/bin/env python3
"""Cost per 1M output tokens, and break-even against hosted APIs, from benchmark results.

Usage: cost.py <prices.json> <bench result JSON>...   (the concurrency-32 runs, one per scenario)
Prices come only from prices.json. A price counts only with its source and the date it was read;
otherwise it prints [X]: numbers are never guessed. Runs with failed requests, or not at
concurrency 32 (full load), are refused.
- $ per 1M output tokens = node $/hour / (output tokens/s x 3600) x 1,000,000, spot and on-demand.
  The headline counts the GPU node only. A second line adds the system node and Cloud NAT's hourly
  charge; it still leaves out NAT data per GiB, the GKE management fee and disks.
- Break-even: what a hosted API would charge per hour for the same traffic (the measured input and
  output tokens over the measured time), and the share of that traffic the GPU node must carry to
  cost less: node $/hour / API $/hour. It assumes the node is paid for every hour and can carry at
  most the measured full load. Below that share the API is cheaper; above 100% it always is.
  This compares prices only, not answer quality.
"""
import json
import sys

X = "[X]"
LOAD = 32  # the concurrency the costs are computed at


def price(entry, key):
    # A price without its source and date isn't a price we can publish.
    if entry.get(key) is None or not entry.get("source") or not entry.get("date"):
        return None
    return entry[key]


def per_m_output(per_hour, run):
    return None if per_hour is None else per_hour / (run["output_throughput"] * 3600) * 1_000_000


def hosted_per_hour(api, run):
    p_in, p_out = price(api, "input_per_1m"), price(api, "output_per_1m")
    if p_in is None or p_out is None:
        return None
    return (run["total_input_tokens"] * p_in + run["total_output_tokens"] * p_out) / 1_000_000 / (run["duration"] / 3600)


def money(v):
    return X if v is None else f"${v:,.2f}"


def share(node, api):
    return X if node is None or api is None else f"{node / api:.0%}"


def total(*parts):
    return None if None in parts else sum(parts)


def report(prices, runs):
    gpu, system, nat = prices["gpu_node"], prices["system_node"], prices["nat"]
    spot, on_demand = price(gpu, "spot_per_hour"), price(gpu, "on_demand_per_hour")
    extra = total(price(system, "per_hour"), price(nat, "per_hour"))
    lines = []
    for run in runs:
        where = f"{run.get('scenario')} c{run.get('max_concurrency')}"
        if run.get("failed", 1) != 0:
            sys.exit(f"{where}: failed requests (or no count); not a valid run")
        if run.get("max_concurrency") != LOAD:
            sys.exit(f"{where}: costs are computed at concurrency {LOAD} (full load) only")
        if not run.get("output_throughput") or not run.get("duration"):
            sys.exit(f"{where}: no throughput or duration in the result")
        lines += [
            (f"## {run['scenario']}, concurrency {LOAD}: {run['output_throughput']:.1f} output tokens/s "
             f"(config `{run['config_hash']}`)"),
            "",
            "| $ per 1M output tokens | Spot | On-demand |",
            "|---|---|---|",
            f"| GPU node only | {money(per_m_output(spot, run))} | {money(per_m_output(on_demand, run))} |",
            (f"| Node hours + NAT hourly (no NAT data per GiB, GKE fee or disks) "
             f"| {money(per_m_output(total(spot, extra), run))} | {money(per_m_output(total(on_demand, extra), run))} |"),
            "",
            "| Hosted API | Its $/hour for this traffic | Break-even share of this load: spot node | on-demand node |",
            "|---|---|---|---|",
        ]
        for api in prices["hosted"]:
            h = hosted_per_hour(api, run)
            lines.append(f"| {api['name']} | {money(h)} | {share(spot, h)} | {share(on_demand, h)} |")
        lines += ["", ("Break-even = GPU node $/hour / API $/hour for this traffic. It assumes the node is paid for "
                       "every hour and carries at most this measured load; prices only, not answer quality."), ""]
    sources = [gpu, system, nat] + prices["hosted"]
    lines.append("Prices: " + "; ".join(f"{p['name']} ({p.get('source') or X}, read {p.get('date') or X})" for p in sources))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    with open(sys.argv[1]) as f:
        prices = json.load(f)
    runs = []
    for path in sys.argv[2:]:
        with open(path) as f:
            runs.append(json.load(f))
    print(report(prices, runs), end="")
