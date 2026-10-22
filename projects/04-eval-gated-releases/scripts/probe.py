#!/usr/bin/env python3
"""The canary's TTFT probe (P4-08). It runs in a short Job inside the session cluster, which promote.py
starts, so no port-forward adds latency. TTFT is the time to first token: here, the time until the first
streamed chunk with answer text, so it includes any thinking the model does first.

It streams REQUESTS short requests to each deployment in TARGETS (by its id, so each one is measured
however the weights stand), taking turns and one at a time. Then it prints one JSON line: for each
deployment, the streams that worked, the errors, and the p95 TTFT in seconds (the 95th percentile,
nearest rank). Nothing else is printed: no prompt, answer or key.
Env: GATEWAY_URL, GATEWAY_KEY, TARGETS (deployment ids, space-separated), REQUESTS (per deployment).
"""
import json
import math
import os
import time
import urllib.request

PROMPT = "List three Kubernetes workload resources, one per line."


def ttft(url, key, model):
    body = {"model": model, "stream": True, "temperature": 0, "max_tokens": 1024,
            "messages": [{"role": "user", "content": PROMPT}]}
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=60) as r:  # closed at the first text: the rest isn't needed
        for line in r:
            if line.startswith(b"data: {") and any(c.get("delta", {}).get("content")
                                                    for c in json.loads(line[6:]).get("choices", [])):
                return time.monotonic() - start
    raise ValueError("the stream had no answer text")


def p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1] if values else None


def main():
    url, key = os.environ["GATEWAY_URL"], os.environ["GATEWAY_KEY"]
    targets, n = os.environ["TARGETS"].split(), int(os.environ["REQUESTS"])
    times, errors = {t: [] for t in targets}, dict.fromkeys(targets, 0)
    for _ in range(n):
        for t in targets:
            try:
                times[t].append(ttft(url, key, t))
            except (OSError, ValueError):  # OSError covers HTTP errors and timeouts
                errors[t] += 1
    print(json.dumps({t: {"n": len(times[t]), "errors": errors[t], "ttft_p95_s": p95(times[t])} for t in targets}))


if __name__ == "__main__":
    main()
