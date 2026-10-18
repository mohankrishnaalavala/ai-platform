"""A tiny HTTP client for the gateway tests (stdlib only). Error bodies are dropped: LiteLLM's can
quote part of a key, and tests must never print one.
Env: GATEWAY_URL (e.g. http://127.0.0.1:4000), MASTER_KEY."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

# The tests create teams with the real onboarding script, so CI runs it too.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import onboard_team

URL = os.environ.get("GATEWAY_URL", "")
MASTER = os.environ.get("MASTER_KEY", "")


def call(method, path, key=None, body=None, timeout=90):
    """Returns (status, body, headers, seconds). body is parsed JSON, the raw text for streams, or None on errors."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(URL + path, method=method, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            ctype = r.headers.get("Content-Type", "")
            parsed = json.loads(raw or "{}") if ctype.startswith("application/json") else raw
            return r.status, parsed, lower(r.headers), time.monotonic() - start
    except urllib.error.HTTPError as e:
        return e.code, None, lower(e.headers), time.monotonic() - start


def lower(headers):
    return {k.lower(): v for k, v in headers.items()}  # header names are case-insensitive


def chat(key, model, text, stream=False, max_tokens=5):
    return call("POST", "/v1/chat/completions", key, {
        "model": model, "max_tokens": max_tokens, "stream": stream,
        "messages": [{"role": "user", "content": text}]})


def new_key(alias):
    status, body, _, _ = call("POST", "/key/generate", MASTER, {"key_alias": alias})
    assert status == 200, f"key/generate returned {status}"
    return body["key"]


def delete_key(key):
    call("POST", "/key/delete", MASTER, {"keys": [key]})


def new_team_key(name, budget_usd=10.0, rpm=100):
    """A team made by onboard_team.py, and one key for it. The name gets a suffix, so re-runs don't clash."""
    name = f"{name}-{int(time.time() * 1000) % 10**8}"
    team_id = onboard_team.create_team(URL, MASTER, name, budget_usd, rpm)
    return onboard_team.create_key(URL, MASTER, team_id, f"{name}-1")
