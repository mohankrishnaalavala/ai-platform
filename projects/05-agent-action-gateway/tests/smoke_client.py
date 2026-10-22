"""The kind smoke test's agent: calls the action gateway through kubectl port-forward, as an agent would.
It runs in the gateway image (which has the MCP SDK); kind-smoke.sh starts it. AGENT_TOKEN holds the token.

  smoke_client.py URL dev    get_status, each write tool and undo in dev, and RBAC refusing kube-system
  smoke_client.py URL prod   two prod scales that wait for the CLI approver: kind-smoke.sh rejects the
                             first (its dry run changed nothing) and approves the second"""
import json
import os
import sys

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

CLOCK = "20261104T160000Z-"  # tests/kind/gateway.yaml's test-only clock: Wednesday 2026-11-04 10:00 in Chicago


async def main(url, step):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {os.environ['AGENT_TOKEN']}"}, timeout=180)
    async with Client(streamable_http_client(url, http_client=http)) as c:
        async def call(tool, **args):
            r = (await c.call_tool(tool, args)).structured_content
            print(f"{tool} {json.dumps(args)} -> {r.get('decision')} {(r.get('result') or {}).get('status', '')}"
                  f" {'; '.join(r.get('reasons') or [])}", flush=True)
            return r

        def check(ok, what, r):
            if not ok:
                sys.exit(f"FAIL: {what}\n{json.dumps(r, indent=2)}")

        async def replicas(ns):
            r = await call("get_status", namespace=ns, name="checkout")
            return r["deployments"][0]["replicas"]

        def executed(r):
            return r["result"]["status"] == "executed"

        if step == "prod":
            r = await call("scale_deployment", namespace="prod", name="checkout", replicas=3)
            check(r["decision"] == "needs_approval" and r["result"]["status"] == "rejected"
                  and r["approver"]["approver"] == "ci-smoke", "prod scale, rejected", r)
            check(r["dry_run"]["ok"] and r["dry_run"]["pods_affected"] == 1 and '+    "replicas": 3' in r["diff"],
                  "the dry run, as the scoped ServiceAccount", r)
            check(r["risk_summary"] == "unavailable", "risk summary falls back (no P2 gateway in kind)", r)
            check(await replicas("prod") == 2, "the dry run and the rejection changed nothing", r)
            r = await call("scale_deployment", namespace="prod", name="checkout", replicas=3)
            check(executed(r) and r["approver"]["approver"] == "ci-smoke", "prod scale, approved", r)
            check(await replicas("prod") == 3, "prod scaled", r)
            return

        r = await call("get_status", namespace="dev")
        check(r["decision"] == "allow" and [d["name"] for d in r["deployments"]] == ["checkout"], "get_status", r)
        check(r["pod_disruption_budgets"][0]["name"] == "checkout", "get_status shows the PDB", r)

        r = await call("scale_deployment", namespace="dev", name="checkout", replicas=3)
        check(executed(r) and r["action_id"].startswith(CLOCK) and await replicas("dev") == 3, "scale (and the test clock)", r)
        r = await call("undo_action", action_id=r["action_id"])
        check(executed(r) and await replicas("dev") == 2, "undo of the scale", r)
        r = await call("restart_deployment", namespace="dev", name="checkout")
        check(executed(r), "restart", r)
        for _ in range(30):  # rollout_undo waits until the controller has recorded the restart's revision
            if (await call("get_status", namespace="dev", name="checkout"))["deployments"][0]["revision"] == "2":
                break
            await anyio.sleep(1)
        r = await call("rollout_undo", namespace="dev", name="checkout")
        check(executed(r), "rollout undo", r)
        cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "smoke-flags"}, "data": {"slow_mode": "off"}}
        r = await call("apply_manifest", namespace="dev", manifest=cm)
        check(executed(r), "apply a new ConfigMap", r)
        r = await call("undo_action", action_id=r["action_id"])
        check(executed(r), "undo of the create (a delete, dry-run first)", r)

        r = await call("scale_deployment", namespace="kube-system", name="coredns", replicas=0)
        check(r["result"]["status"] == "denied" and "forbidden" in r["dry_run"]["error"], "RBAC refuses kube-system", r)


if __name__ == "__main__":
    anyio.run(main, sys.argv[1], sys.argv[2])
    print("smoke client OK")
