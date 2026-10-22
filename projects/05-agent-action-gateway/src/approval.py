#!/usr/bin/env python3
"""Approvers: who answers when the policy says needs_approval. An approver has one method,
decide(action_id, request, timeout_s), which returns {"approved": bool, "approver": name or None,
"via": how}. No answer within the timeout (15 minutes in the server) is a reject. The Slack approver
(Socket Mode) comes later with the same method.

The CLI (command line) approver, for kind and CI: the server writes each pending request as a file in
APPROVALS_DIR, and a person answers by running this file inside the pod:

  kubectl -n action-gateway exec deploy/action-gateway -c gateway -- python approval.py list
  kubectl -n action-gateway exec deploy/action-gateway -c gateway -- python approval.py approve <action_id> --by <name>

The agent can't do this: it reaches only the MCP port, never `kubectl exec`. The approver's name is what
--by says, which is fine for kind and CI but not for production (Slack gives a real identity)."""
import argparse
import json
import os
import sys
import time

import audit


class CliApprover:
    def __init__(self, directory, poll_s=1.0):
        self.dir = directory
        self.poll_s = poll_s

    def paths(self, action_id):
        return (os.path.join(self.dir, f"{action_id}.request.json"),
                os.path.join(self.dir, f"{action_id}.answer.json"))

    def decide(self, action_id, request, timeout_s):
        os.makedirs(self.dir, exist_ok=True)
        req, ans = self.paths(action_id)
        with open(req, "x") as f:
            json.dump(request, f, indent=2)
        deadline = time.monotonic() + timeout_s
        try:
            while time.monotonic() < deadline:
                if os.path.exists(ans):
                    with open(ans) as f:
                        a = json.load(f)
                    return {"approved": a["decision"] == "approve", "approver": a["by"], "via": "cli"}
                time.sleep(self.poll_s)
            return {"approved": False, "approver": None, "via": f"auto-reject: no answer in {timeout_s:g} s"}
        finally:
            for p in (req, ans):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass


def main(argv=None):
    p = argparse.ArgumentParser(description="Answer the action gateway's pending approvals (kind and CI).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="print every pending request")
    for cmd in ("approve", "reject"):
        s = sub.add_parser(cmd)
        s.add_argument("action_id")
        s.add_argument("--by", required=True, help="your name, recorded as the approver")
    args = p.parse_args(argv)
    approver = CliApprover(os.environ.get("APPROVALS_DIR", "/data/approvals"))
    if args.cmd == "list":
        names = sorted(os.listdir(approver.dir)) if os.path.isdir(approver.dir) else []
        for name in names:
            if name.endswith(".request.json"):
                with open(os.path.join(approver.dir, name)) as f:
                    print(f.read())
        return 0
    if not audit.ID.match(args.action_id) or not args.by.strip():
        print("need an action id (from list) and a non-empty --by", file=sys.stderr)
        return 2
    req, ans = approver.paths(args.action_id)
    if not os.path.exists(req):
        print(f"no pending request {args.action_id}: answered, expired or unknown", file=sys.stderr)
        return 1
    with open(ans + ".tmp", "w") as f:
        json.dump({"decision": args.cmd, "by": args.by.strip()}, f)
    os.replace(ans + ".tmp", ans)  # atomic, so the server never reads half an answer
    print(f"{args.cmd}: {args.action_id}, by {args.by.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
