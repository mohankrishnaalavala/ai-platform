#!/usr/bin/env python3
"""P2-01 spike checks against LiteLLM on localhost:4000 (compose.yaml), with no license key.

Usage: MASTER_KEY=... check.py phase1   (the mock vLLM is up)
       MASTER_KEY=... check.py phase2   (the mock vLLM has been stopped: fallback)
Prints one line per check (PASS / FAIL / INFO) and writes out/<phase>.json. Keys are never printed.
The PII is fake test data.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:4000"
MOCKS = {"vllm": "http://127.0.0.1:8101", "hosted": "http://127.0.0.1:8102"}
ANALYZER = "http://127.0.0.1:8103"  # Presidio loads a language model first; early calls would fail
MASTER = os.environ["MASTER_KEY"]
PII = {"email": "jane.doe@example.com", "phone": "212-555-0143", "card": "4111 1111 1111 1111", "name": "Jane Doe"}
RESULTS = []


def call(method, path, key, body=None, base=GATEWAY, timeout=60):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(), dict(r.headers), time.monotonic() - start
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers), time.monotonic() - start


def report(status, name, detail):
    RESULTS.append({"status": status, "check": name, "detail": detail})
    print(f"{status:4} {name}: {detail}", flush=True)


def chat(key, model, text, stream=False):
    return call("POST", "/v1/chat/completions", key,
                {"model": model, "messages": [{"role": "user", "content": text}], "stream": stream})


def new_key(**team):
    team_id = None
    if team:
        s, b, _, _ = call("POST", "/team/new", MASTER, team)
        assert s == 200, f"team/new {s}: {b[:200]}"
        team_id = json.loads(b)["team_id"]
    s, b, _, _ = call("POST", "/key/generate", MASTER, {"team_id": team_id} if team_id else {})
    assert s == 200, f"key/generate {s}: {b[:200]}"
    key = json.loads(b)["key"]
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{key}", flush=True)
    return key


def pii_prompt(tag):
    return (f"[{tag}] Please draft a reply to {PII['name']}. Her email is {PII['email']}, "
            f"her phone is {PII['phone']} and her card number is {PII['card']}.")


def raw_pii_seen(mock, tag):
    """Which fake PII values reached this mock unmasked, in requests carrying this tag."""
    with urllib.request.urlopen(MOCKS[mock] + "/log", timeout=10) as r:
        bodies = [json.loads(line) for line in r.read().decode().splitlines() if line]
    texts = [json.dumps(b["messages"]) for b in bodies if tag in json.dumps(b.get("messages", ""))]
    return len(texts), sorted({kind for kind, value in PII.items() for t in texts if value in t})


def wait_until_ready():
    for base, path in ((GATEWAY, "/health/liveliness"), (ANALYZER, "/health")):
        for _ in range(150):
            try:
                if call("GET", path, MASTER, base=base, timeout=5)[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(2)
        else:
            sys.exit(f"{base}{path} not healthy after 300 s")


def phase1():
    wait_until_ready()
    report("PASS", "gateway and Presidio up", "both health checks 200")

    key = new_key(team_alias="spike-budget", max_budget=0.01)
    codes = []
    for _ in range(8):
        s, b, _, _ = chat(key, "chat-default", "hello")
        codes.append(s)
        if s != 200:
            ok = codes.count(200) > 0 and s in (400, 429) and "budget" in b.lower()
            report("PASS" if ok else "FAIL", "team budget ($0.01, $0.006 a call)",
                   f"{codes.count(200)} calls ok, then {s}: {b[:160]}")
            break
        time.sleep(1.5)  # spend is written in batches every second (proxy_batch_write_at)
    else:
        report("FAIL", "team budget ($0.01, $0.006 a call)", f"never blocked in 8 calls: {codes}")

    key = new_key(team_alias="spike-rpm", rpm_limit=2)
    codes = [chat(key, "chat-default", "hello")[0] for _ in range(5)]
    report("PASS" if codes[:2] == [200, 200] and 429 in codes[2:] else "FAIL", "team RPM limit (2 a minute, 5 calls)",
           f"status codes {codes}")

    key = new_key()
    before = chat(key, "chat-default", "hello")[0]
    s_del, _, _, _ = call("POST", "/key/delete", MASTER, {"keys": [key]})
    s, b, _, _ = chat(key, "chat-default", "hello")
    report("PASS" if before == 200 and s_del == 200 and s in (400, 401, 403) else "FAIL", "revoked key rejected",
           f"before {before}, key/delete {s_del}, after {s}: {b[:160]}")

    key = new_key()
    for stream in (False, True):
        tag = f"hosted-{'stream' if stream else 'plain'}"
        s, b, _, _ = chat(key, "chat-hosted", pii_prompt(tag), stream)
        n, raw = raw_pii_seen("hosted", tag)
        report("PASS" if s == 200 and n and not raw else "FAIL", f"PII masked on chat-hosted ({tag})",
               f"status {s}, {n} request(s) at the hosted mock, raw PII there: {raw or 'none'}")
        tag = f"default-{'stream' if stream else 'plain'}"
        s, _, _, _ = chat(key, "chat-default", pii_prompt(tag), stream)
        n, raw = raw_pii_seen("vllm", tag)
        report("INFO", f"PII on chat-default to vLLM ({tag})",
               f"status {s}, {n} request(s) at the vLLM mock, raw PII there: {raw or 'none'}")


def phase2():
    key = new_key()
    for stream in (False, True):
        tag = f"fallback-{'stream' if stream else 'plain'}"
        s, b, h, t = chat(key, "chat-default", pii_prompt(tag), stream)
        n, raw = raw_pii_seen("hosted", tag)
        answered = "hosted" if "reply from hosted" in b else ("vllm" if "reply from vllm" in b else "?")
        report("PASS" if s == 200 and answered == "hosted" else "FAIL", f"fallback to chat-hosted ({tag})",
               f"status {s}, answered by {answered}, {t * 1000:.0f} ms, "
               f"attempted-fallbacks header {h.get('x-litellm-attempted-fallbacks', 'absent')}")
        report("PASS" if n and not raw else "FAIL", f"PII masked on the fallback ({tag})",
               f"{n} request(s) at the hosted mock, raw PII there: {raw or 'none'}")


if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        {"phase1": phase1, "phase2": phase2}.get(phase, lambda: sys.exit(__doc__))()
    finally:
        os.makedirs("out", exist_ok=True)
        with open(f"out/{phase}.json", "w") as f:
            json.dump(RESULTS, f, indent=1)
    sys.exit(any(r["status"] == "FAIL" for r in RESULTS))  # a FAIL turns the job red
