#!/usr/bin/env python3
"""Onboard a team on the LLM gateway: create the team with a budget and rate limits, and one key for
it. Prints the key once; the gateway keeps only its hash. Stdlib only.

Usage: LITELLM_MASTER_KEY=... GATEWAY_URL=http://127.0.0.1:14000 onboard_team.py NAME BUDGET_USD RPM [TPM]
`make team NAME=payments BUDGET_USD=20 RPM=60` sets both variables and the port-forward.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")  # e.g. payments, data-science


def admin(url, master, method, path, body=None):
    req = urllib.request.Request(url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {master}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        # The status only: LiteLLM's error bodies can quote part of a key.
        sys.exit(f"{method} {path}: HTTP {e.code}")


def create_team(url, master, name, budget_usd, rpm, tpm=None):
    """Create the team; refuse a name that already exists. Returns the team id."""
    teams = admin(url, master, "GET", "/team/list")
    if not isinstance(teams, list):
        sys.exit("unexpected reply from /team/list")
    if any(t.get("team_alias") == name for t in teams):
        sys.exit(f"team {name} already exists")
    body = {"team_alias": name, "max_budget": budget_usd, "rpm_limit": rpm}
    if tpm is not None:
        body["tpm_limit"] = tpm
    return admin(url, master, "POST", "/team/new", body)["team_id"]


def create_key(url, master, team_id, alias):
    """One key for the team. The budget and limits are the team's, shared by all its keys."""
    return admin(url, master, "POST", "/key/generate", {"team_id": team_id, "key_alias": alias})["key"]


def parse(argv):
    if len(argv) not in (3, 4):
        sys.exit(__doc__)
    name = argv[0]
    if not NAME.match(name):
        sys.exit(f"NAME must be 3-40 lowercase letters, digits or dashes: {name!r}")
    try:
        budget, rpm = float(argv[1]), int(argv[2])
        tpm = int(argv[3]) if len(argv) == 4 else None
    except ValueError:
        sys.exit("BUDGET_USD must be a number, RPM and TPM whole numbers")
    if budget <= 0 or rpm <= 0 or (tpm is not None and tpm <= 0):
        sys.exit("BUDGET_USD, RPM and TPM must be more than 0")
    return name, budget, rpm, tpm


def main(argv):
    name, budget, rpm, tpm = parse(argv)
    url, master = os.environ.get("GATEWAY_URL", ""), os.environ.get("LITELLM_MASTER_KEY", "")
    if not url or not master:
        sys.exit("set GATEWAY_URL and LITELLM_MASTER_KEY (make team does both)")
    start = time.monotonic()
    team_id = create_team(url, master, name, budget, rpm, tpm)
    key = create_key(url, master, team_id, f"{name}-1")
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{key}")
    print(f"team {name} ({team_id}): budget ${budget:g}, {rpm} requests a minute"
          + (f", {tpm} tokens a minute" if tpm else "") + f"; onboarded in {time.monotonic() - start:.1f} s")
    print(f"key (shown once; the gateway keeps only its hash): {key}")


if __name__ == "__main__":
    main(sys.argv[1:])
