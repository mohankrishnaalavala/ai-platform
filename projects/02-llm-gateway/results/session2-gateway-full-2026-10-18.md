# P2 session 2: gateway with real vLLM (2026-10-18), partly valid

**Run:** GitHub Actions [39168677257](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39168677257), `gpu-session.yml` mode `gateway-full`, zone `us-central1-c`, **on-demand** L4 (user decision), from `main` at commit `d8850cf`.

**Setup:**
- P1 serves `qwen2.5-7b-fp8` on a `g2-standard-8` node, the GPU node.
- P2 runs the gateway, with the test-only mock, on the `e2-standard-4` system node.
- GKE `v1.36.4-gke.1495002`.
- The software is the same as in session 1.

**Outcome:**
- `make test P=02` passed.
- The overhead benchmark **did not run**: its preflight stopped it, as designed, before any benchmark time.
- The fallback after vLLM went away **ran**, but its first check failed for the same reason.

The cause was my test order, not the gateway (see "What went wrong"). The lines below are copied from the run's log, which GitHub keeps for 90 days.

## Every P2 check, with real vLLM behind chat-default
```text
test_the_default_alias_answers ... ok        (chat-default answered by vLLM)
test_the_hosted_alias_answers ... ok         (Gemini on Vertex AI, keyless)
budget $1e-05: status codes [200, 200, 200, 429]
rpm 2: status codes [200, 200, 429, 429]
direct (plain and streaming): raw values that got through, per type: card 0/16, email 0/20, phone 0/20
team shakedown-221133 (<team id>): budget $1, 10 requests a minute; onboarded in 0.5 s
PASS: make team (end to end 3.5 s)
dump: 989 lines, 34 spend-log rows
logs, traces and spend logs: raw values that got through, per type: card 0/8, email 0/10, phone 0/10
fail closed (pii-fail-closed-plain): status 500, requests at the provider 0
fail closed (pii-fail-closed-stream): status 500, requests at the provider 0
all P2 checks passed
```
The budget figure: here `chat-default` calls were answered by vLLM, at the internal price ($0.00000075 per output token).

## Fallback after vLLM went away (P2-07)
KEDA was paused at 0 replicas, so the vLLM pod shut down normally (not a crash). Then:
```text
fallback (plain): status 200, 31568 ms, attempted fallbacks 1
fallback (stream): status 200, 671 ms, attempted fallbacks 1
PASS: fallback to the hosted model after vLLM went away
```
**What it shows:**
- **The plain request waited the full 30 s timeout.** LiteLLM still held an open connection to the old vLLM pod's address. That address no longer answers, so the request hung until the timeout.
- **The streaming request took 0.7 s.** It opened a new connection, which Kubernetes refused at once, because the Service had no pods left.

So even a normal scale-down costs a non-streaming user about the `timeout` once. This matches CI's hung-backend case (`results/ci-gateway-2026-10-18.md`).

**Not valid:** the check before it ("chat-default answered by vLLM itself") got HTTP 500. That was the analyzer restart below, not vLLM.

## What went wrong
`make test`'s last check scales the PII analyzer to 0 (fail-closed), then back to 1. It didn't wait for the analyzer to be ready again. The analyzer needs time to load its language model, and the gateway refuses every request while it's down. That's fail-closed, as designed. So the next steps' first calls got HTTP 500:
```text
22:12:31  deployment.apps/presidio-analyzer scaled              (back to 1)
22:12:33  presidio-analyzer: Starting gunicorn 25.1.0            (then loading its model)
22:12:38  overhead preflight: HTTP Error 500                     (no benchmark time was spent)
22:12:41  vLLM up: status 500, 189 ms, attempted fallbacks absent
```
- **What this means for operations:** a restart of the PII analyzer makes the whole gateway refuse requests for about a minute. Two analyzer replicas would avoid that, at about 734Mi more memory.
- **Fix:** the tests now wait until the analyzer is ready, after the fail-closed check and before the benchmark.

## Resource use (`kubectl top` in diagnostics, at the end of the session)
| Pod | CPU | Memory |
|---|---|---|
| litellm | 22m | 574Mi |
| presidio-analyzer | 2m | 734Mi |
| otel-collector | 3m | 81Mi |
| postgres | 21m | 77Mi |
| presidio-anonymizer | 1m | 43Mi |
| mock-provider | 1m | 17Mi |
| KEDA (operator, metrics server) | 5m, 7m | 28Mi, 33Mi |
| Prometheus (operator, server) | 4m, 7m | 15Mi, 39Mi |

**System node:** requests 2546m CPU (64%) and 6.12 GB memory (44%) of the `e2-standard-4`, with P1's Prometheus and KEDA and all of P2 on it.

## Timing and teardown
- **Timing:** `make up` started at 21:38:30Z. The P2 checks ran from 22:11:02Z to 22:12:31Z.
- **Teardown:** the teardown job's `make status` at 22:25:17Z reported "nothing billable is running".
