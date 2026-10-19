#!/usr/bin/env python3
"""The P2 demo, printed as a transcript for the blog's screenshots (session gateway-full, through
`run.sh demo`, which sets GATEWAY_URL, MASTER_KEY and MOCK_HOSTED_URL). Fake personal data only.
No key is printed: make team's key line is dropped (its ::add-mask:: line is passed on first), and
anything key-like or a team ID is blanked in every line.
  demo.py            vLLM up: onboard a team, a call vLLM answers, a tiny budget blocked, PII masked
  demo.py vllm-down  after `run.sh vllm-down` took vLLM away: 10 plain, then 10 streaming calls to
                     chat-default, each with its time, the model that answered and the fallbacks tried
Exits 1 if a step didn't show what it should.
"""
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

from client import URL, chat, delete_key, new_key
from test_pii import PROMPTS, received

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
QUESTION = "In one sentence, what does an LLM gateway do?"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
problems = []


def clean(text):
    text = re.sub(r"sk-[\w.-]{4,}", "sk-<redacted>", text)
    text = re.sub(r"\b[0-9a-f]{40,}\b", "<hash>", text)  # LiteLLM can quote a key's hash
    return UUID.sub("<team id>", text)


def show(text=""):
    print(clean(text), flush=True)


def expect(ok, what):
    if not ok:
        problems.append(what)
        show(f"UNEXPECTED: {what}")


def team(name, budget, rpm):
    """`make team`, as a team lead runs it. Returns the key; shows everything else."""
    show(f"$ make team NAME={name} BUDGET_USD={budget} RPM={rpm}")
    start = time.monotonic()
    r = subprocess.run(["make", "-C", REPO, "--no-print-directory", "team", f"NAME={name}",
                        f"BUDGET_USD={budget}", f"RPM={rpm}"], capture_output=True, text=True, check=False)
    lines = r.stdout.splitlines()
    for line in lines:
        if line.startswith("::add-mask::"):
            print(line, flush=True)  # first, so the Actions runner hides the key everywhere
    key = next((line.rsplit(" ", 1)[1] for line in lines if line.startswith("key ")), "")
    for line in lines + r.stderr.splitlines():
        if not line.startswith(("key ", "::add-mask::")):
            show(line)
    if r.returncode or not key.startswith("sk-"):
        sys.exit("make team failed")
    show(f"(the key stays in memory, not shown; {time.monotonic() - start:.1f} s end to end)")
    return key


def sse(raw):
    """The text of a streamed answer."""
    chunks = [json.loads(line[len("data: "):]) for line in raw.splitlines() if line.startswith("data: {")]
    return "".join(((c.get("choices") or [{}])[0].get("delta") or {}).get("content") or "" for c in chunks)


def ask(key, model, text, stream=False, max_tokens=60, quiet=False):
    """One call, shown as status, time, answering model, fallbacks tried and the answer.
    Returns (status, answering model, attempted fallbacks, seconds)."""
    if not quiet:
        show(f'$ ask {model} "{text}"' + (" (streaming)" if stream else ""))
    status, body, headers, seconds = chat(key, model, text, stream, max_tokens)
    by, answer = "-", ""
    if status == 200:  # the body's model is the alias; this header names the model that answered
        by = headers.get("x-litellm-model-name", "?")
        answer = sse(body) if stream else body["choices"][0]["message"]["content"]
    fallbacks = headers.get("x-litellm-attempted-fallbacks", "absent")
    show(f"HTTP {status} in {seconds * 1000:.0f} ms, answered by {by}, attempted fallbacks {fallbacks}")
    if answer and not quiet:
        show(answer.strip())
    return status, by, fallbacks, seconds


def refusal(key, model, text):
    """LiteLLM's error message for one refused call (client.py drops error bodies; clean() blanks keys)."""
    req = urllib.request.Request(URL + "/v1/chat/completions", method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 data=json.dumps({"model": model, "max_tokens": 5,
                                                  "messages": [{"role": "user", "content": text}]}).encode())
    try:
        urllib.request.urlopen(req, timeout=60).read()
        return "not refused"
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
        return f"HTTP {e.code}: {(body.get('error') or {}).get('message', '')}"


def vllm_up():
    show("== 1. A new team gets a key, a budget and a rate limit with one command")
    key = team("payments", 20, 60)

    show("\n== 2. The team calls one model name; our own GPU (vLLM) answers")
    status, _, fallbacks, _ = ask(key, "chat-default", QUESTION)
    expect(status == 200 and fallbacks in ("0", "absent"), "vLLM didn't answer chat-default")

    show("\n== 3. Budgets are hard limits: a team with $0.0001")
    intern = team("intern", 0.0001, 60)
    codes = []
    for _ in range(10):
        codes.append(ask(intern, "chat-default", "Count from 1 to 40.", max_tokens=100, quiet=True)[0])
        if codes[-1] != 200:
            break
        time.sleep(1.5)  # spend is written in batches
    show(f"status codes {codes}")
    expect(codes[-1] == 429, "the tiny budget wasn't enforced in 10 calls")
    show(refusal(intern, "chat-default", "One more?"))

    show("\n== 4. Personal data is masked before a prompt leaves: Gemini on Vertex AI gets placeholders")
    text = f"Repeat exactly: {PROMPTS[0]['text']} {PROMPTS[1]['text']}"  # fake email and phone (pii_prompts.json)
    status, _, _, _ = ask(key, "chat-hosted", text)
    expect(status == 200, "chat-hosted didn't answer")

    show("\n== 5. Proof: the test provider records exactly what it received")
    ask(key, "chat-hosted-mock", f"[demo] {PROMPTS[2]['text']} {PROMPTS[0]['text']}", max_tokens=5)
    got = received("demo")
    show(f"the provider received: {got[-1] if got else 'nothing'}")
    raw = [v for p in (PROMPTS[0], PROMPTS[2]) for vs in p["pii"].values() for v in vs if got and v in got[-1]]
    expect(got and not raw, f"raw values reached the provider: {len(raw)}")


def vllm_down():
    show("== 6. vLLM is gone (KEDA paused at 0 replicas); the same model name, 10 plain and 10 streaming calls")
    key = new_key("demo-vllm-down")
    try:
        results = {False: [], True: []}
        for stream in (False, True):
            for i in range(1, 11):
                print(f"{'streaming' if stream else 'plain'} {i}: ", end="")
                results[stream].append(ask(key, "chat-default", "Reply with the single word: ok", stream,
                                           max_tokens=5, quiet=True))
    finally:
        delete_key(key)
    for stream, rs in results.items():
        ok = [r for r in rs if r[0] == 200]
        times = sorted(r[3] * 1000 for r in rs)
        show(f"{'streaming' if stream else 'plain'}: {len(ok)}/{len(rs)} answered; time min / median / max "
             f"{times[0]:.0f} / {statistics.median(times):.0f} / {times[-1]:.0f} ms; answered by "
             f"{sorted({r[1] for r in ok})}")
        expect(len(ok) == len(rs), "a call failed while vLLM was down")


if __name__ == "__main__":
    vllm_down() if sys.argv[1:] == ["vllm-down"] else vllm_up()
    sys.exit(1 if problems else 0)
