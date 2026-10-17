#!/usr/bin/env python3
"""Cold-start phases from Kubernetes timestamps, for one vLLM pod that just started from zero.

Usage: timings.py <pod.json> <events.json> <node.json> <asked-at epoch> <first-token epoch> [key=value ...]
Prints one JSON object. t0 is the pod's creation (the moment a replica was asked for):
- node ready:   t0 -> PodScheduled (a GPU node exists, its driver is in and the GPU is allocatable)
- image pulled: PodScheduled -> the vllm container's Pulled event. With image streaming the image is
                "pulled" early and its files are read later, so that time lands in "model loaded".
- model loaded: Pulled -> pod Ready (vLLM answers /health once the weights are on the GPU)
- first token:  Ready -> the first one-token completion returned (timed on the pod's clock)
Kubernetes timestamps have 1-second resolution, and readiness is probed every 5 s, so "model loaded"
can read up to about 5 s long.
A run is valid only as a real cold start: a GPU node created after the replica was asked for, the
image not already on that node, and the container never restarted.
"""
import json
import re
import sys
from datetime import datetime, timezone


def ts(s):
    # "2026-10-17T01:02:03Z" or, for eventTime, "2026-10-17T01:02:03.123456Z"
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def phases(pod, events, node, asked_at, first_token):
    t0 = ts(pod["metadata"]["creationTimestamp"])
    cond = {c["type"]: ts(c["lastTransitionTime"]) for c in pod["status"]["conditions"] if c["status"] == "True"}
    pulled = [e for e in events["items"]
              if e["reason"] == "Pulled" and e["involvedObject"].get("fieldPath") == "spec.containers{vllm}"]
    if "PodScheduled" not in cond or "Ready" not in cond or not pulled:
        sys.exit("the pod isn't scheduled, pulled and ready yet")
    pulled_at = ts(pulled[-1].get("lastTimestamp") or pulled[-1]["eventTime"])
    p = {
        "node_ready_s": cond["PodScheduled"] - t0,
        "image_pulled_s": pulled_at - cond["PodScheduled"],
        "model_loaded_s": cond["Ready"] - pulled_at,
        "first_token_s": first_token - cond["Ready"],
        "total_s": first_token - t0,
    }
    # The kubelet's own note (e.g. pull time and size); the image path is dropped: it names the project.
    note = re.sub(r'"[^"]*"', '"<image>"', pulled[-1].get("message", ""))
    restarts = sum(c.get("restartCount", 0) for c in pod["status"].get("containerStatuses", []))
    problems = []
    if ts(node["metadata"]["creationTimestamp"]) < asked_at - 1:
        problems.append("the GPU node existed before the replica was asked for (warm node)")
    if "already present" in note:
        problems.append("the image was already on the node (warm image)")
    if restarts:
        problems.append(f"the pod's containers restarted {restarts} time(s) (spot interruption?)")
    return ({k: round(v, 1) for k, v in p.items()}
            | {"pulled_message": note, "restarts": restarts, "valid": not problems, "problems": problems})


if __name__ == "__main__":
    if len(sys.argv) < 6:
        sys.exit(__doc__)
    docs = []
    for path in sys.argv[1:4]:
        with open(path) as f:
            docs.append(json.load(f))
    extra = dict(a.split("=", 1) for a in sys.argv[6:])
    result = extra | phases(*docs, float(sys.argv[4]), float(sys.argv[5]))
    if extra.get("vllm_pods_seen", "1") != "1":
        result["problems"].append("more than one vLLM pod: one was replaced on the way up (spot interruption?)")
        result["valid"] = False
    print(json.dumps(result, indent=1))
