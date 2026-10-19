# P2 session 3: gateway with real vLLM (2026-10-19), partly valid

**Run:** GitHub Actions [39183088327](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39183088327), `gpu-session.yml` mode `gateway-full`, zone `us-central1-c`, **on-demand** L4 (user decision), from `main` at commit `3eebf89`.

**Setup:** the same as session 2 (`results/session2-gateway-full-2026-10-18.md`).
- P1 serves `qwen2.5-7b-fp8` on a `g2-standard-8` node, the GPU node.
- P2 runs the gateway, with the test-only mock, on the `e2-standard-4` system node.
- GKE `v1.36.4-gke.1495002`.

**Outcome:**
- **Every P2 check passed**, but the `make test` step failed after them: the PII analyzer didn't come back within 10 min of the fail-closed check (see "What went wrong").
- **vLLM answered first, then the fallback worked** (P2-07). Valid.
- **The overhead benchmark ran both halves but is INVALID**, for two reasons:
  1. Through the gateway, every streamed answer arrived in one piece. So its "time to first token" is the whole answer, and the run doesn't measure what the gateway adds.
  2. `overhead.sh` counted 3 requests that fell back to the hosted model during the gateway half. Its rule makes any fallback INVALID.

Both causes are found or instrumented in PR #40. The lines below are copied from the run's log, which GitHub keeps for 90 days. The lines quoted here are saved in [`overhead/20261019t0058z/session-log-excerpt.txt`](overhead/20261019t0058z/session-log-excerpt.txt).

## The P2 checks, with real vLLM behind chat-default
Selected lines from `make test P=02`, with the team ID taken out. Every check passed.
```text
budget $1e-05: status codes [200, 200, 200, 429]
direct (plain and streaming): raw values that got through, per type: card 0/16, email 0/20, phone 0/20
rpm 2: status codes [200, 200, 429, 429]
PASS: gateway tests (smoke, budget, rate limit, keys, PII direct)
team shakedown-013202 (<team id>): budget $1, 10 requests a minute; onboarded in 0.6 s
PASS: make team (end to end 3.6 s)
dump: 990 lines, 34 spend-log rows
logs, traces and spend logs: raw values that got through, per type: card 0/8, email 0/10, phone 0/10
PASS: no raw PII in logs, traces or spend logs
fail closed (pii-fail-closed-plain): status 500, requests at the provider 0
fail closed (pii-fail-closed-stream): status 500, requests at the provider 0
PASS: fail closed (PII analyzer scaled to 0)
```
These ran with LiteLLM's default Presidio scope ("both": requests and answers masked). PR #40 masks requests only.

## vLLM first, then fallback after vLLM went away (P2-07)
KEDA was paused at 0 replicas, so the vLLM pod shut down normally (not a crash). Then:
```text
vLLM up: status 200, 256 ms, attempted fallbacks 0
PASS: chat-default answered by vLLM itself
fallback (plain): status 200, 31520 ms, attempted fallbacks 1
fallback (stream): status 200, 681 ms, attempted fallbacks 1
PASS: fallback to the hosted model after vLLM went away
```
- **The same shape as session 2:** 31,568 ms plain and 671 ms streaming there. Two runs, so this pattern repeated once.
- **What it means:** a normal scale-down cost the non-streaming request about the 30 s `timeout`, and the streaming one under a second.

## Overhead benchmark (P2-08): INVALID
Raw files: [`overhead/20261019t0058z/direct/20261019t014359z/`](overhead/20261019t0058z/direct/20261019t014359z/) and [`overhead/20261019t0058z/gateway/20261019t015258z/`](overhead/20261019t0058z/gateway/20261019t015258z/) (`summary.md`, the `vllm bench serve` JSON, the Job log).

**Method:** the same `vllm bench serve` Job, inside the cluster. Chat: 512 tokens in, 128 out, `ignore_eos`. Concurrency 10 then 50. First straight to vLLM, then through the gateway (`chat-default`).

| Half | Concurrency | Requests ok / failed | TTFT p50 / p95 / p99 (ms) | End-to-end p50 / p95 / p99 (ms) | TPOT p50 (ms) | Output tokens |
|---|---|---|---|---|---|---|
| direct | 10 | 100 / 0 | 706 / 890 / 978 | 5560 / 5580 / 5655 | 38.3 | 12,800 |
| direct | 50 | 500 / 0 | 1078 / 2320 / 3902 | 10407 / 11597 / 13204 | 72.7 | 64,000 |
| gateway | 10 | 100 / 0 | 6023 / 7521 / 8335 | 6023 / 7521 / 8335 | 0.0 | 12,116 |
| gateway | 50 | 500 / 0 | 12999 / 18143 / 19325 | 12999 / 18143 / 19325 | 0.0 | 60,893 |

TTFT: time to first token. TPOT: time per output token.

**Why it's invalid:**
- **One piece:** in the gateway half, inter-token latency is 0 at every percentile, and mean TTFT equals mean end-to-end time (6,176.7 ms at 10 concurrent, 13,149.0 ms at 50). The client got each answer all at once, when it was done. No added latency is derived from this run.
- **Not the same work:** the gateway half counted fewer output tokens (12,116 against 12,800 at 10). LiteLLM does pass `ignore_eos` on to vLLM (checked locally against the mock). So the likely cause is how the benchmark counted tokens in the rebuilt one-piece answer, but that wasn't checked.
- **Fallbacks:** `requests that fell back to the hosted model during the gateway run: 3`. The script counts spend-log rows whose model isn't Qwen. The run didn't record which model, provider or status those rows had. The gateway's log holds only its last 313 lines and no fallback reason. It does show 2 requests refused for a malformed key at 01:58:57Z, at the end of the run. Whether those are among the 3 is unknown. PR #40 makes the script print what it counted.

**Root cause of the one-piece answers** (found after the session, with LiteLLM v1.103.3 in local containers, adding one config piece at a time):
- Only the Presidio guardrail causes it. The timeouts, fallbacks, OTel callback, message-logging switch, database and virtual keys each still streamed.
- LiteLLM sets a Presidio guardrail's `presidio_filter_scope` to "both" unless told otherwise. That adds a second Presidio pass, on every answer, which collects the whole stream before sending it on.
- With `presidio_filter_scope: input` the stream arrived in pieces again. CI now has a test for this (`tests/test_streaming.py`). It failed on the old config in CI runs 39189519299 and 39189642556 ("1 not greater than or equal to 2").

## What went wrong: the PII analyzer hung on restart
`make test`'s fail-closed check scales the analyzer to 0, then back to 1, and now waits for it (the session 2 fix). This time the new container never answered its health check:
```text
01:33:01  deployment.apps/presidio-analyzer scaled                     (back to 1)
01:33:01  Waiting for deployment "presidio-analyzer" rollout to finish: 0 of 1 updated replicas are available...
01:43:01  error: timed out waiting for the condition                   (the 10 min wait gave up)
```
- **The pod's events:** "Startup probe failed: ... context deadline exceeded", then "failed startup probe, will be restarted". The container ran from 01:33:01 to 01:43:32 and ended with exit code 137.
- **The restart worked:** the next container started at 01:43:32. The overhead step's own wait saw the rollout finish at 01:43:51.
- **Why it hung is unknown:** the diagnostics kept only the running container's log. CI once saw Presidio's anonymizer hang at start in the same way (`results/ci-gateway-2026-10-18.md`).
- **Fix (PR #40):** the waits cover one restart (20 min), and diagnostics keep a restarted container's log.

## Resource use (`kubectl top`, right after the gateway half)
| Pod | CPU | Memory |
|---|---|---|
| litellm | 783m | 709Mi |
| presidio-analyzer | 407m | 952Mi |
| postgres | 28m | 98Mi |
| presidio-anonymizer | 9m | 43Mi |
| otel-collector | 3m | 98Mi |
| mock-provider | 1m | 18Mi |

**System node:** requests 2546m CPU (64%) and 6.12 GB memory (44%) of the `e2-standard-4`, the same as session 2.

## Timing and teardown
- **Timing:** `make up` started at 00:58:20Z and finished at about 01:31:31Z. The P2 checks ran from 01:31:43Z to 01:33:00Z. The direct half ran 01:43:59Z–01:52:41Z and the gateway half 01:52:58Z–01:59:05Z. The fallback check ended at 02:00:02Z.
- **Teardown:** the teardown job's `make status` at 02:10:44Z reported "nothing billable is running".
