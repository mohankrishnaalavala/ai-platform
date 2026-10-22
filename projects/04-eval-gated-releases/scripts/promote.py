#!/usr/bin/env python3
"""The canary promoter (P4-08, ADR-0004). release-model.yml runs it through promote.sh, which opens
port-forwards to the gateway and to Prometheus and reads the gateway's master key.

1. It adds two deployments of chat-hosted to the gateway's database: the baseline model at weight 100
   and the candidate at 0. LiteLLM changes a database deployment's weight at runtime, with no restart.
   chat-hosted's own deployment in the config file has no weight, so it gets no traffic while these
   exist (tests/test_canary_contract.py proves both).
2. The candidate's share goes 10 -> 50 -> 100 percent. After each step, three checks:
   - Error rate, from Prometheus (P3's span metrics, label litellm_provider_model): the candidate's failed
     attempts out of all its attempts in the step. At most --max-error-rate (default 0.5%, the error budget
     of P3's gateway availability SLO). It first waits until Prometheus shows at least the attempts the step
     sent the candidate (probe and live eval), up to --settle-timeout; fewer fails the check.
   - TTFT (time to first token) p95: a short Job in the cluster (probe.py) streams to each deployment by
     its id. Any failed stream fails the check (no p95 from a partial sample). Then the candidate's p95
     must be at most the baseline's x --ttft-ratio (default 1.2).
   - Live eval: 20 golden-set cases with deterministic checks only, sent to each deployment by its id, at
     temperature 0. It fails if any call on either side gets no answer, if the candidate passes no case,
     or if in any category the candidate passes fewer than the baseline. A case the scorer can't score
     stops the run before the canary starts.
3. Any failed check, or any error after the canary has started: the candidate's weight goes to 0 (all
   traffic to the baseline), then a GitHub issue opens with the timeline, and it exits 1.
   All passed: the candidate keeps 100% until the session ends; making that permanent is a change to
   chat-hosted in P2's values.yaml, through the eval gate.
--rollback only sets the candidate's weight to 0 (release-model.yml's last step, after a cancel).

Env: GATEWAY_URL, PROMETHEUS_URL, MASTER_KEY (never printed). It prints case ids and numbers only, never
a prompt or an answer. Standard library only.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIAS, BASELINE_ID, CANDIDATE_ID = "chat-hosted", "canary-baseline", "canary-candidate"
# As chat-hosted in P2's helm/gateway/values.yaml: keyless Vertex AI and its timeouts.
HOSTED_PARAMS = {"vertex_project": "os.environ/VERTEX_PROJECT", "vertex_location": "global",
                 "timeout": 60, "stream_timeout": 10}
NAMESPACE, PROBE = "gateway", "canary-probe"
PROBE_IMAGE = "python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f"  # P2's mock pin
ERROR = "STATUS_CODE_ERROR"
MODEL = re.compile(r"[a-z0-9_]+/[A-Za-z0-9._-]+")  # provider/model, e.g. vertex_ai/gemini-3.5-flash (fullmatch)

sys.path.insert(0, str(HERE.parent / "evals"))
try:  # stream S1's deterministic scorers (P4-02): check(answer, check_) -> bool, ValueError if it can't score
    from scorers import check as passes
except ModuleNotFoundError as e:
    if e.name != "scorers":
        raise

    def passes(answer, check_):
        """shortcut: a stand-in with S1's interface until its evals/scorers.py is on main; delete it then.
        Simple check kinds only (substrings, not whole tokens; JSON parsed, not schema-checked). Any other
        kind raises, as S1's does, so the canary refuses to start rather than score it."""
        text, kind = answer.strip(), check_["type"]
        rules = {
            "must_contain": lambda: all(v.lower() in text.lower() for v in check_["values"]),
            "must_not_contain": lambda: not any(v.lower() in text.lower() for v in check_["values"]),
            "max_words": lambda: len(text.split()) <= check_["value"],
            "min_words": lambda: len(text.split()) >= check_["value"],
            "regex": lambda: re.search(check_["pattern"], text) is not None,
            "json_schema": lambda: is_json_object(re.sub(r"^```(?:json)?\s*|\s*```$", "", text)),
        }
        if kind not in rules:
            raise ValueError(f"the stand-in scorer can't score {kind!r} checks")
        return bool(text) and rules[kind]()

    def is_json_object(text):
        try:
            return isinstance(json.loads(text), dict)
        except ValueError:
            return False


class GatewayError(RuntimeError):
    def __init__(self, method, path, code):
        super().__init__(f"{method} {path}: HTTP {code}")  # no body: LiteLLM's can quote part of a key
        self.code = code


def api(method, path, body=None, key=None, timeout=120):
    req = urllib.request.Request(os.environ["GATEWAY_URL"] + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + (key or os.environ["MASTER_KEY"]),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise GatewayError(method, path, e.code) from None


def add_deployments(baseline, candidate, params=None, alias=ALIAS):
    """The canary's two deployments of the alias, all traffic on the baseline. A re-run starts clean."""
    for mid in (BASELINE_ID, CANDIDATE_ID):
        try:
            api("POST", "/model/delete", {"id": mid})
        except GatewayError:
            pass  # not there
    for mid, model, weight in ((BASELINE_ID, baseline, 100), (CANDIDATE_ID, candidate, 0)):
        api("POST", "/model/new", {"model_name": alias, "model_info": {"id": mid},
                                   "litellm_params": {**(params or HOSTED_PARAMS), "model": model, "weight": weight}})


def set_weights(pct, current):
    """The candidate's share in percent. The side going up changes first, so both are never 0 at once
    (then LiteLLM would pick among all of the alias's deployments, the candidate too)."""
    pairs = [(CANDIDATE_ID, pct), (BASELINE_ID, 100 - pct)]
    for mid, weight in pairs if pct > current else reversed(pairs):
        api("PATCH", f"/model/{mid}/update", {"litellm_params": {"weight": weight}})


def new_key(budget):
    """A key for the probe and live eval: only the canary's deployments and alias, a USD cap, 2 hours."""
    return api("POST", "/key/generate", {"key_alias": f"canary-{int(time.time())}", "max_budget": budget,
                                         "models": [BASELINE_ID, CANDIDATE_ID, ALIAS], "duration": "2h"})["key"]


def attempts(model, alias=ALIAS):
    """The deployment's attempt counts so far, by span status, from P3's span metrics. Calls to the
    alias, and calls that name the candidate's id (probe and live eval), both count."""
    selector = (f'span_name="litellm_request",litellm_model_group=~"{alias}|{CANDIDATE_ID}",'
                f'litellm_provider_model="{model}"')
    query = urllib.parse.urlencode({"query": f"sum by (status_code) (traces_span_metrics_calls_total{{{selector}}})"})
    with urllib.request.urlopen(f"{os.environ['PROMETHEUS_URL']}/api/v1/query?{query}", timeout=30) as r:
        return {s["metric"].get("status_code", ""): float(s["value"][1]) for s in json.load(r)["data"]["result"]}


def wait_for_attempts(model, before, expected, timeout):
    """The counts once Prometheus shows at least `expected` new attempts since `before`, or at the timeout
    (spans reach it in batches: LiteLLM's exporter, then the collector's 15 s flush, then the scrape)."""
    deadline = time.monotonic() + timeout
    while True:
        after = attempts(model)
        if sum(after.values()) - sum(before.values()) >= expected or time.monotonic() >= deadline:
            return after
        time.sleep(5)


def sh(*cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=True).stdout


def probe_manifest(key, requests, owner, session):
    """The probe Job, its script and its key (a Secret, sent through stdin, never in an argument)."""
    labels = {"app": "ai-platform", "layer": "04", "owner": owner, "session": session, "app.kubernetes.io/name": PROBE}
    meta = {"name": PROBE, "namespace": NAMESPACE, "labels": labels}
    env = {"GATEWAY_URL": "http://gateway:4000", "TARGETS": f"{BASELINE_ID} {CANDIDATE_ID}", "REQUESTS": str(requests)}
    container = {
        "name": "probe", "image": PROBE_IMAGE, "command": ["python3", "/probe/probe.py"],
        "env": [{"name": k, "value": v} for k, v in env.items()], "envFrom": [{"secretRef": {"name": PROBE}}],
        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "128Mi"}},
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
        "volumeMounts": [{"name": "probe", "mountPath": "/probe", "readOnly": True}]}
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": meta, "spec": {
        "backoffLimit": 0, "activeDeadlineSeconds": 600, "ttlSecondsAfterFinished": 3600,  # in case promote.py dies
        "template": {"metadata": {"labels": labels}, "spec": {
            "restartPolicy": "Never", "automountServiceAccountToken": False, "enableServiceLinks": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 65534, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [container], "volumes": [{"name": "probe", "configMap": {"name": PROBE}}]}}}}
    return [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta, "data": {"probe.py": (HERE / "probe.py").read_text()}},
            {"apiVersion": "v1", "kind": "Secret", "metadata": meta, "stringData": {"GATEWAY_KEY": key}}, job]


def run_probe(key, requests):
    """Start the probe Job, wait for it, return its results; the Job, script and key are deleted after."""
    labels = json.loads(sh("kubectl", "get", "namespace", NAMESPACE, "-o", "json"))["metadata"].get("labels", {})
    if not (labels.get("owner") and labels.get("session")):
        raise RuntimeError(f"namespace {NAMESPACE} has no owner or session label (deploy P2 first)")
    clean = ("kubectl", "delete", "job,configmap,secret", "-n", NAMESPACE, "-l", f"app.kubernetes.io/name={PROBE}", "--ignore-not-found")
    sh(*clean, "--wait")
    try:
        sh("kubectl", "apply", "-f", "-", stdin=json.dumps({"apiVersion": "v1", "kind": "List",
                                                         "items": probe_manifest(key, requests, labels["owner"], labels["session"])}))
        deadline = time.monotonic() + 900
        # "<succeeded>/<failed>": "/" while it runs
        while (status := sh("kubectl", "get", "job", PROBE, "-n", NAMESPACE, "-o",
                            "jsonpath={.status.succeeded}/{.status.failed}").strip()) == "/":
            if time.monotonic() > deadline:
                raise RuntimeError("the probe Job didn't finish in 15 min")
            time.sleep(5)
        logs = sh("kubectl", "logs", "-n", NAMESPACE, f"job/{PROBE}")
        if status != "1/":
            raise RuntimeError(f"the probe Job failed ({status}): {logs[-500:]}")
        return json.loads(logs.strip().splitlines()[-1])
    finally:
        sh(*clean, "--wait=false")


def select_cases(pattern, n):
    """n cases with deterministic checks, spread evenly over the golden set in id order."""
    cases = sorted((json.loads(line) for f in sorted(glob.glob(pattern)) for line in Path(f).read_text().splitlines()
                    if line.strip()), key=lambda c: c["id"])
    cases = [c for c in cases if c["check"]["type"] != "judge"]
    if len(cases) < n:
        raise SystemExit(f"the live eval needs {n} cases with deterministic checks in {pattern}; found {len(cases)}")
    picked = [cases[i * len(cases) // n] for i in range(n)]
    for c in picked:
        try:
            passes("", c["check"])  # an empty answer never passes: this only asks whether the case can be scored
        except ValueError as e:
            raise SystemExit(f"the live eval can't score {c['id']}: {e}") from None
    return picked


def live_eval(cases, key, log):
    """Passed cases per category, and calls without an answer, for each deployment; each called by its id
    at temperature 0."""
    passed = {BASELINE_ID: collections.Counter(), CANDIDATE_ID: collections.Counter()}
    errors = dict.fromkeys(passed, 0)
    for case in cases:
        messages = [{"role": "system", "content": case["system"]}] if case.get("system") else []
        messages.append({"role": "user", "content": case["prompt"]})
        result = {}
        for mid, counts in passed.items():
            try:
                body = api("POST", "/v1/chat/completions", {"model": mid, "messages": messages, "temperature": 0,
                                                            "max_tokens": 2048}, key)
                result[mid] = bool(passes(body["choices"][0]["message"]["content"] or "", case["check"]))
            except (GatewayError, OSError, ValueError, KeyError, IndexError):  # no answer: fails the case and the step
                result[mid] = False
                errors[mid] += 1
            counts[case["category"]] += result[mid]
        log("case", id=case["id"], baseline=result[BASELINE_ID], candidate=result[CANDIDATE_ID])
    return passed, errors


def checks(errors, total, expected, probe, passed, eval_errors, categories, a):
    """The failed checks, in words; empty if all passed. Each one fails closed on missing data."""
    failed = []
    if errors < 0 or total < max(expected, 1):
        failed.append(f"error rate: Prometheus showed {total:.0f} of the {expected} candidate attempts the step sent "
                      f"within {a.settle_timeout} s (or its counters reset)")
    elif errors / total > a.max_error_rate:
        failed.append(f"error rate: {errors:.0f} of {total:.0f} candidate attempts failed, over {a.max_error_rate:.2%}")
    base, cand = probe[BASELINE_ID], probe[CANDIDATE_ID]
    if base["errors"] or cand["errors"] or base["ttft_p95_s"] is None or cand["ttft_p95_s"] is None:
        failed.append(f"TTFT: failed streams, baseline {base['errors']} and candidate {cand['errors']}: "
                      "no p95 from a partial sample")
    elif cand["ttft_p95_s"] > base["ttft_p95_s"] * a.ttft_ratio:
        failed.append(f"TTFT p95: candidate {cand['ttft_p95_s']:.2f} s, over baseline {base['ttft_p95_s']:.2f} s "
                      f"x {a.ttft_ratio}")
    if any(eval_errors.values()):
        failed.append(f"live eval: calls without an answer, baseline {eval_errors[BASELINE_ID]} and "
                      f"candidate {eval_errors[CANDIDATE_ID]}")
    if not sum(passed[CANDIDATE_ID].values()):
        failed.append("live eval: the candidate passed no case")
    for c in categories:
        if passed[CANDIDATE_ID][c] < passed[BASELINE_ID][c]:
            failed.append(f"live eval, {c}: candidate passed {passed[CANDIDATE_ID][c]}, baseline {passed[BASELINE_ID][c]}")
    return failed


def open_issue(a, pct, failed, timeline, rolled_back):
    run = os.environ.get("GITHUB_RUN_ID") and (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
                                               f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    state = (f"The candidate's weight is now 0: the baseline `{a.baseline}` takes all of the alias's traffic."
             if rolled_back else "**Setting the candidate's weight to 0 failed**: run `promote.sh --rollback`.")
    body = "\n".join([
        f"The canary of `{a.candidate}` behind `{ALIAS}` failed at {pct}%. {state}", "", "Failed checks:",
        *[f"- {f}" for f in failed], "", "Timeline (UTC):", "```", *timeline, "```", *([f"Run: {run}"] if run else [])])
    repo = ["--repo", os.environ["GITHUB_REPOSITORY"]] if os.environ.get("GITHUB_REPOSITORY") else []
    sh("gh", "issue", "create", *repo, "--title", f"Canary rolled back: {a.candidate} on {ALIAS} at {pct}%",
       "--body-file", "-", stdin=body)


def model(value):
    if not MODEL.fullmatch(value):
        raise argparse.ArgumentTypeError(f"not a provider/model name: {value!r}")
    return value


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--baseline", type=model, default="vertex_ai/gemini-3.5-flash-lite")
    p.add_argument("--candidate", type=model, default="vertex_ai/gemini-3.5-flash")
    p.add_argument("--steps", type=lambda s: [int(x) for x in s.split(",")], default=[10, 50, 100])
    p.add_argument("--max-error-rate", type=float, default=0.005)
    p.add_argument("--ttft-ratio", type=float, default=1.2)
    p.add_argument("--probe-requests", type=int, default=20)
    p.add_argument("--live-cases", type=int, default=20)
    p.add_argument("--cases", default=str(HERE.parent / "evals" / "datasets" / "v1" / "*.jsonl"))
    p.add_argument("--settle-timeout", type=int, default=180, help="seconds to wait for the step's spans in Prometheus")
    p.add_argument("--key-budget", type=float, default=5, help="USD cap on the probe and live eval's key")
    p.add_argument("--rollback", action="store_true", help="only set the candidate's weight to 0")
    return p.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    if a.rollback:
        try:
            set_weights(0, 100)
            print("the candidate's weight is 0")
        except GatewayError as e:
            if e.code != 404:
                raise
            print("no canary deployments: nothing to roll back")
        return 0
    if sorted(a.steps) != a.steps or not 0 < a.steps[0] <= a.steps[-1] <= 100:
        raise SystemExit("--steps must rise, within 1..100")
    timeline = []

    def log(event, **fields):
        line = " ".join([datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), event,
                         *(f"{k}={v}" for k, v in fields.items())])
        timeline.append(line)
        print(line, flush=True)

    cases = select_cases(a.cases, a.live_cases)  # before anything changes on the gateway
    categories = sorted({c["category"] for c in cases})
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # a cancelled run still rolls back (below)
    key = new_key(a.key_budget)
    pct, started = 0, False
    try:
        add_deployments(a.baseline, a.candidate)
        started = True
        log("start", baseline=a.baseline, candidate=a.candidate, steps=a.steps)
        before = attempts(a.candidate)
        for step in a.steps:
            set_weights(step, pct)
            pct = step
            log("weights", candidate_pct=step)
            probe = run_probe(key, a.probe_requests)
            log("probe", **{f"{k}_{m}": v for k, r in probe.items() for m, v in r.items()})
            passed, eval_errors = live_eval(cases, key, log)
            expected = a.probe_requests + len(cases)  # what this step sent the candidate itself
            after = wait_for_attempts(a.candidate, before, expected, a.settle_timeout)
            errors, total = after.get(ERROR, 0) - before.get(ERROR, 0), sum(after.values()) - sum(before.values())
            before = after
            log("checks", candidate_errors=errors, candidate_attempts=total, expected_attempts=expected,
                **{f"eval_{c}": f"{passed[CANDIDATE_ID][c]}/{passed[BASELINE_ID][c]}" for c in categories})
            if failed := checks(errors, total, expected, probe, passed, eval_errors, categories, a):
                return roll_back(a, pct, failed, timeline, log)
        log("promoted", candidate=a.candidate, candidate_pct=pct)
        return 0
    except BaseException as e:  # an error, Ctrl-C or SIGTERM mid-canary: the candidate goes back to 0
        if not started:
            raise
        return roll_back(a, pct, [f"error: {type(e).__name__}: {e}"], timeline, log)
    finally:
        try:
            api("POST", "/key/delete", {"keys": [key]})
        except (GatewayError, OSError):
            print("couldn't revoke the canary's key; it goes with the cluster")


def roll_back(a, pct, failed, timeline, log):
    for f in failed:
        log("failed", check=f)
    try:
        set_weights(0, pct)
        log("rolled back", candidate_pct=0)
        rolled_back = True
    except (GatewayError, OSError) as e:  # still open the issue; release-model.yml's last step tries again
        log("rollback failed", error=e)
        rolled_back = False
    open_issue(a, pct, failed, timeline, rolled_back)
    return 1


if __name__ == "__main__":
    sys.exit(main())
