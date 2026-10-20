"""P3-08's probe (scale_from_zero.sh runs it once vLLM and its GPU node are gone): chat-default through the
gateway every 2 s, until vLLM answers itself (x-litellm-attempted-fallbacks: 0) or TIMEOUT_S. One line per
probe, then a summary: how fast the first request was answered (by the hosted fallback) and how long until
the self-hosted model served again. `python3 scale_from_zero.py summary FILE` summarizes a saved log.
Env: GATEWAY_URL, MASTER_KEY, P2_TESTS (P2's test helpers), TIMEOUT_S (default 1200).
"""
import os
import re
import sys
import time

LINE = re.compile(r"t=(?P<t>[\d.]+)s status=(?P<status>\d+) ms=(?P<ms>\d+) fallbacks=(?P<fallbacks>\S+) model=(?P<model>\S+)")


def summarize(lines):
    """The two numbers P3 publishes, from probe lines: the first answer, and when vLLM answered again."""
    probes = [m.groupdict() for m in map(LINE.search, lines) if m]
    if not probes:
        return None
    back = next((p for p in probes if p["status"] == "200" and p["fallbacks"] == "0"), None)
    return {
        "probes": len(probes),
        "answered": sum(p["status"] == "200" for p in probes),
        "first_status": int(probes[0]["status"]),
        "first_ms": int(probes[0]["ms"]),
        "first_fallbacks": probes[0]["fallbacks"],
        "self_hosted_after_s": float(back["t"]) if back else None,
    }


def report(s):
    if not s:
        return "no probes"
    after = f"{s['self_hosted_after_s'] / 60:.1f} min" if s["self_hosted_after_s"] is not None else "[X] (not within the run)"
    return (f"first request: status {s['first_status']} in {s['first_ms']} ms, attempted fallbacks {s['first_fallbacks']}\n"
            f"self-hosted serving again after: {after}\n"
            f"probes answered: {s['answered']} of {s['probes']}")


def probe():
    sys.path.insert(0, os.environ["P2_TESTS"])
    from client import chat, delete_key, new_key
    key = new_key("scale-from-zero")
    start, lines = time.monotonic(), []
    try:
        for n in range(int(float(os.environ.get("TIMEOUT_S", "1200")) / 2)):
            time.sleep(max(0.0, start + 2 * n - time.monotonic()))  # one probe every 2 s, from the first
            t = time.monotonic() - start
            status, _, headers, seconds = chat(key, "chat-default", "Reply with the single word: ok")
            fallbacks = headers.get("x-litellm-attempted-fallbacks", "absent")
            lines.append(f"t={t:.1f}s status={status} ms={seconds * 1000:.0f} fallbacks={fallbacks} "
                         f"model={headers.get('x-litellm-model-name', 'absent')}")
            print(lines[-1], flush=True)
            if status == 200 and fallbacks == "0":
                break
    finally:
        delete_key(key)
    s = summarize(lines)
    print(report(s))
    return 0 if s and s["self_hosted_after_s"] is not None else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["summary"]:
        with open(sys.argv[2]) as f:
            print(report(summarize(f.read().splitlines())))
    else:
        sys.exit(probe())
