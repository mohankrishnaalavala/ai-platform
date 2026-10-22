"""Runs each scenario's tool call through the Rego policy (OPA in Docker) and checks the decision and reasons.

No cluster. The facts the gateway would read from the cluster come from evals/base.yaml plus the scenario's
setup. The dry run is the one fact that needs a live cluster: this check takes the scenario's expect.dry_run
(a clean dry run where it has none), and the scenario runner on kind checks the real one.
tests/policy.sh runs this and sets OPA_IMAGE.
"""
import json
import os
import subprocess
import sys

from test_scenarios import RESULT_FIELDS, ROOT, load_base, load_scenarios

CLEAN_DRY_RUN = {"ok": True, "error": "", "pdb_violations": [], "pods_affected": 0}
QUERY = "{n: r | some n, f in input; r := data.gateway.result with input as f}"


def facts(s):
    """The OPA input the gateway would build for this scenario."""
    objects = {}
    for o in load_base() + s["setup"]:  # a setup object replaces the base object of the same kind and name
        m = o["metadata"]
        objects[(o["kind"], m.get("namespace", ""), m["name"])] = o
    args = s["call"]["arguments"]
    ns = args.get("namespace", "")
    labels = objects.get(("Namespace", "", ns), {}).get("metadata", {}).get("labels", {})
    f = {"tool": s["call"]["tool"], "args": args, "namespace": {"name": ns}, "time": s["time"], "team": s["team"],
         "dry_run": {**CLEAN_DRY_RUN, **s["expect"].get("dry_run", {})}}
    if "env" in labels:
        f["namespace"]["env"] = labels["env"]
    target = args.get("name")
    manifest = args.get("manifest")
    if isinstance(manifest, dict) and manifest.get("kind") == "Deployment":
        target = manifest.get("metadata", {}).get("name")
        f["current"] = {"replicas": 0}  # a Deployment that doesn't exist yet
    deployment = objects.get(("Deployment", ns, target))
    if deployment:
        f["current"] = {"replicas": deployment["spec"].get("replicas", 1)}
    return f


def main():
    image = os.environ.get("OPA_IMAGE") or sys.exit("OPA_IMAGE is not set: run tests/policy.sh, which sets it")
    scenarios = {s["name"]: s for s in load_scenarios().values()}
    run = subprocess.run(
        ["docker", "run", "--rm", "-i", "-v", f"{ROOT / 'policies'}:/policies:ro", image,
         "eval", "--format", "raw", "--data", "/policies", "--stdin-input", QUERY],
        input=json.dumps({n: facts(s) for n, s in scenarios.items()}), capture_output=True, text=True, check=False)
    if run.returncode:
        sys.exit(run.stderr)
    results = json.loads(run.stdout)
    failed = 0
    for n, s in scenarios.items():
        r, expect = results[n], s["expect"]
        problems = [f"missing keyword {k!r}" for k in expect["reasons"]
                    if k.lower() not in " | ".join(r["reasons"]).lower()]
        problems += [f"{k} is {r[k]!r}, expected {expect[k]!r}" for k in RESULT_FIELDS
                     if k in expect and r[k] != expect[k]]
        ok = r["decision"] == expect["decision"] and not problems
        failed += not ok
        declared = " [dry run from the scenario]" if "dry_run" in expect else ""
        print(f"{'ok  ' if ok else 'FAIL'} {n}: {r['decision']}{declared} ({'; '.join(r['reasons'])})"
              + (f": {'; '.join(problems)}" if problems else ""))
    print(f"{len(scenarios) - failed} of {len(scenarios)} scenarios match the policy")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
