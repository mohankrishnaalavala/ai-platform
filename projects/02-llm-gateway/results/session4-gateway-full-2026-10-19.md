# P2 session 4: gateway with real vLLM (2026-10-19), partly valid

**Run:** GitHub Actions [39342772607](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39342772607), `gpu-session.yml` mode `gateway-full`, zone `us-central1-c`, **on-demand** L4 (user decision, budget $3–5), from `main` at commit `a8201ef`.

**Setup:** the same as sessions 2 and 3 (`results/session2-gateway-full-2026-10-18.md`).
- **GPU node:** P1 serves `qwen2.5-7b-fp8` (config hash `5d6172c8cb52818f`) with vLLM 0.30.0 on a `g2-standard-8` node with 1x L4.
- **System node:** P2 runs the gateway and the test-only mock on the `e2-standard-4` system node: LiteLLM v1.103.3 and Presidio 2.2.362, masking requests only (`presidio_filter_scope: input`, PR #40).
- **GKE:** `v1.36.4-gke.1495002`. Both nodes in `us-central1-c`.

**Outcome:**
- **Valid:** every P2 check passed. The demo ran in full. vLLM answered first, then the fallback worked, and 20 more calls with vLLM gone all succeeded.
- **INVALID:** the overhead benchmark (P2-08). `overhead.sh` counted 3 requests that fell back to the hosted model during the gateway runs, and its rule makes any fallback INVALID. This time they are real fallbacks, answered by Gemini. P2-08 stays open.

Every line quoted below is saved in [`overhead/20261019t2338z/session-log-excerpt.txt`](overhead/20261019t2338z/session-log-excerpt.txt). Team IDs, the project ID and anything key-like are replaced there. GitHub keeps the full log for 90 days.

## The P2 checks, with real vLLM behind chat-default
```text
budget $1e-05: status codes [200, 200, 200, 429]
direct (plain and streaming): raw values that got through, per type: card 0/16, email 0/20, phone 0/20
rpm 2: status codes [200, 200, 429, 429]
PASS: gateway tests (smoke, budget, rate limit, keys, PII direct)
team shakedown-001616 (<team id>): budget $1, 10 requests a minute; onboarded in 0.5 s
PASS: make team (end to end 4.8 s)
dump: 947 lines, 35 spend-log rows
logs, traces and spend logs: raw values that got through, per type: card 0/8, email 0/10, phone 0/10
PASS: no raw PII in logs, traces or spend logs
fail closed (pii-fail-closed-plain): status 500, requests at the provider 0
fail closed (pii-fail-closed-stream): status 500, requests at the provider 0
PASS: fail closed (PII analyzer scaled to 0)
all P2 checks passed
```
- **PII masking:** the 20 fake-PII prompts went to the in-cluster mock provider through the hosted alias. The mock records what it receives, and no raw value reached it (PII: personally identifiable information).
- **The analyzer restart:** after the fail-closed check, the PII analyzer came back in 11 s (00:17:19 to 00:17:30). In session 3 it hung for 10 min.

## The demo (P2-10), printed for the blog's screenshots
`tests/run.sh demo`, then `tests/run.sh demo vllm-down` after vLLM went away. All personal data is fake (`tests/pii_prompts.json`). No key was printed.

**1. Onboarding.** `make team NAME=payments BUDGET_USD=20 RPM=60`: "onboarded in 0.5 s" (the gateway calls), 3.6 s end to end with the port-forward and the master key.

**2. vLLM answers.**
```text
HTTP 200 in 1258 ms, answered by openai/Qwen/Qwen2.5-7B-Instruct, attempted fallbacks 0
```

**3. A $0.0001 team is blocked.**
```text
status codes [200, 200, 429]
HTTP 429: Budget has been exceeded! Team=<team id> Current cost: 0.00015000000000000001, Max budget: 0.0001
```
- **Where the cost comes from:** each call cost $0.000075, the internal price of 100 output tokens at $0.00000075 each.
- **Why the block came on the third call:** after the first call the team had spent $0.000075, still under its budget. After the second it had spent $0.00015, so the third was refused.

**4. Masked on the way to Gemini** (`chat-hosted`):
```text
$ ask chat-hosted "Repeat exactly: Please email the invoice to jane.doe@example.com by Friday. Call me back on (212) 555-0143 after 3pm."
HTTP 200 in 963 ms, answered by vertex_ai/gemini-3.5-flash-lite, attempted fallbacks 0
Please email the invoice to <EMAIL_ADDRESS> by Friday. Call me back on <PHONE_NUMBER> after 3pm.
```

**5. What the test provider received:**
```text
the provider received: [{"role": "user", "content": "[demo] The customer's card number is <CREDIT_CARD>, expiring 09/28. Please email the invoice to <EMAIL_ADDRESS> by Friday."}]
```

Only requests are masked. An answer that repeats or invents personal data reaches the caller unmasked (PR #40).

## vLLM first, then fallback after vLLM went away (P2-07, and P2-03's open question)
KEDA was paused at 0 replicas, so the vLLM pod shut down normally (not a crash). Then:
```text
vLLM up: status 200, 250 ms, attempted fallbacks 0
fallback (plain): status 200, 31108 ms, attempted fallbacks 1
fallback (stream): status 200, 974 ms, attempted fallbacks 1
PASS: fallback to the hosted model after vLLM went away
```
Then the demo's 20 calls to `chat-default` (10 plain, then 10 streaming, one at a time), each timed to the full answer:
```text
plain: 10/10 answered; time min / median / max 706 / 854 / 1059 ms; answered by ['vertex_ai/gemini-3.5-flash-lite']
streaming: 10/10 answered; time min / median / max 700 / 744 / 1135 ms; answered by ['vertex_ai/gemini-3.5-flash-lite']
```
- **22 of 22 requests succeeded while vLLM was down.** Every one was answered by Gemini, with `attempted fallbacks 1`.
- **Only the first plain call paid the timeout.**
  - That call took 31,108 ms (the 30 s `timeout`), the same shape as sessions 2 and 3 (31,568 ms and 31,520 ms).
  - The next 10 plain calls took 706–1,059 ms.
  - This answers P2-03's question: after the first call, requests didn't wait on the dead backend again. Why isn't shown. It could be LiteLLM's cooldown of the failed deployment, or the Service now refusing connections at once. The run doesn't separate the two.
- **The model that answered** comes from the `x-litellm-model-name` header. The answer body names only the alias.

## Overhead benchmark (P2-08): INVALID
Raw files: [`overhead/20261019t2338z/`](overhead/20261019t2338z/), one folder per round (`r1`, `r2`, `r3`), each with `direct/<time>/` and `gateway/<time>/` (`summary.md`, the `vllm bench serve` JSON, the Job log).

**Method:**
- **The load:** the same `vllm bench serve` Job inside the cluster, on the system node. Chat: 512 tokens in, 128 out, `ignore_eos`. Concurrency 10 then 50. 100 and 500 prompts.
- **The two targets:** straight to vLLM, and through the gateway (`chat-default`, with a key made for the run).
- **Three rounds:** round 1 went straight first, round 2 through the gateway first, round 3 straight first.

**Why it's invalid:**
```text
requests that fell back to the hosted model during round 1's gateway run: 2
 vertex_ai/gemini-3.5-flash-lite | vertex_ai           | success | acompletion |     2
requests that fell back to the hosted model during round 2's gateway run: 1
 vertex_ai/gemini-3.5-flash-lite | vertex_ai           | success | acompletion |     1
requests that fell back to the hosted model during round 3's gateway run: 0
INVALID: 3 requests fell back to the hosted model during the gateway runs
```
- **Real fallbacks:** these are successful Gemini answers, not failed requests (session 3 couldn't tell).
- **The benchmark counted them as successes:** "500 / 0" below, because the client got an answer.
- **Which concurrency they happened at wasn't recorded.**

### Reference only: not publishable
These numbers come from an INVALID run. Don't quote them as the gateway's overhead. They're here because they show what to fix.

| Round | Half | Concurrency | Requests ok / failed | TTFT p50 / p95 / p99 (ms) | End to end p50 / p95 (ms) | ITL p50 (ms) | TPOT p50 (ms) | Output tokens/s | Output tokens |
|---|---|---|---|---|---|---|---|---|---|
| 1 | direct | 10 | 100 / 0 | 729 / 936 / 1043 | 5611 / 5664 | 36.359 | 38.4 | 227.9 | 12,800 |
| 1 | direct | 50 | 500 / 0 | 1119 / 2434 / 4096 | 10513 / 11896 | 46.082 | 73.8 | 653.8 | 64,000 |
| 1 | gateway | 10 | 100 / 0 | 717 / 2058 / 2266 | 5932 / 6989 | 36.445 | 42.5 | 195.2 | 12,346 |
| 1 | gateway | 50 | 500 / 0 | 7708 / 11009 / 17417 | 17936 / 20017 | 0.008 | 80.8 | 336.5 | 61,542 |
| 2 | direct | 10 | 100 / 0 | 735 / 933 / 983 | 5616 / 5651 | 36.386 | 38.4 | 227.8 | 12,800 |
| 2 | direct | 50 | 500 / 0 | 1076 / 2399 / 4065 | 10542 / 11847 | 46.023 | 73.9 | 654.7 | 64,000 |
| 2 | gateway | 10 | 100 / 0 | 812 / 1831 / 1848 | 6048 / 6673 | 36.356 | 41.9 | 196.9 | 12,445 |
| 2 | gateway | 50 | 500 / 0 | 7409 / 12366 / 14475 | 17428 / 20832 | 0.008 | 77.3 | 342.6 | 61,327 |
| 3 | direct | 10 | 100 / 0 | 731 / 923 / 1055 | 5605 / 5647 | 36.368 | 38.4 | 228.2 | 12,800 |
| 3 | direct | 50 | 500 / 0 | 1114 / 2380 / 3999 | 10489 / 11687 | 46.052 | 73.3 | 657.9 | 64,000 |
| 3 | gateway | 10 | 100 / 0 | 854 / 1679 / 1852 | 6252 / 6621 | 36.386 | 42.5 | 189.4 | 12,378 |
| 3 | gateway | 50 | 500 / 0 | 7610 / 11183 / 13569 | 17737 / 21945 | 0.008 | 80.5 | 334.1 | 61,160 |

TTFT: time to first token. ITL: inter-token latency. TPOT: time per output token.

`overhead.py` on these rounds gives these differences (gateway minus direct). End-to-end differences are `[X]` in every round, because the output token counts differ:

| Concurrency | Measure | Per round (ms) | Median (ms) |
|---|---|---|---|
| 10 | TTFT p50 | −12, +76, +123 | +76 |
| 10 | TTFT p95 | +1122, +898, +756 | +898 |
| 50 | TTFT p50 | +6589, +6332, +6496 | +6496 |
| 50 | TTFT p95 | +8576, +9967, +8802 | +8802 |

**What the reference numbers show:**
- **At 10 concurrent the stream works.** ITL p50 through the gateway is 36.4 ms, the same as straight to vLLM. So the buffering found in session 3 is gone at this load.
- **At 50 concurrent the gateway falls far behind.**
  - TTFT p50 rose by about 6.5 s in every round.
  - TTFT p99 through the gateway was 13.6–17.4 s, against about 4 s straight to vLLM.
  - Output throughput fell to 334–343 tokens/s, against 654–658 straight.
  - ITL p50 is 0.008 ms: tokens arrive in bursts, not one by one.
  - Requests that wait more than the 10 s `stream_timeout` for a first token fall back to Gemini. That's the likely source of the 3 fallbacks, but not proven.
- **Likely cause: a CPU limit somewhere in the gateway path.** Not proven which part.
  - Right after each gateway run, `kubectl top` showed LiteLLM at 681–704m CPU (one Python process).
  - Right after the same runs, the Presidio analyzer showed 125–182m. It analyzes every prompt, and it showed 407m in session 3.
  - Both readings were taken after the load, not during it.
  - The benchmark client shares the same 4-CPU system node, and the node's CPU requests are at 64%.
  - So LiteLLM, Presidio and the shared node are all plausible. The run doesn't separate them.
- **Output tokens:** the gateway halves counted 2.8–4.4% fewer output tokens, at both concurrencies and in round 3 too (no fallbacks there). LiteLLM passes `ignore_eos` on (checked locally against the mock). So the likely cause is how the benchmark counts tokens in the gateway's stream, but that wasn't checked.

## Resource use (`kubectl top`, right after each gateway run)
| Pod | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| litellm | 681m, 709Mi | 704m, 719Mi | 701m, 716Mi |
| presidio-analyzer | 141m, 754Mi | 182m, 755Mi | 125m, 755Mi |
| postgres | 22m, 98Mi | 27m, 100Mi | 24m, 97Mi |
| presidio-anonymizer | 5m, 44Mi | 7m, 44Mi | 6m, 44Mi |
| otel-collector | 3m, 103Mi | 2m, 103Mi | 3m, 78Mi |
| mock-provider | 1m, 18Mi | 1m, 18Mi | 1m, 17Mi |

**System node:** requests 2546m CPU (64%) and 6.12 GB memory (44%) of the `e2-standard-4`, the same as sessions 2 and 3.

## Timing, teardown and cost
- **Timing:**
  - The run started at 23:38:02Z. `make up` ran 23:38:59Z–00:15:43Z, and the P2 checks ended at 00:17:30Z.
  - The demo ran 00:17:36Z–00:17:56Z.
  - The overhead rounds ran 00:18:08Z–01:12:29Z.
  - The fallback check ended at 01:13:18Z, and the demo's 20 calls at 01:13:41Z.
- **Teardown:** the teardown job's `make status` at 01:25:33Z reported "nothing billable is running", and so did a local `make status` afterwards.
- **Cost (estimate, list prices before credits, billing not checked):** about 1.8 h of cluster time, about $2.4.
  - g2-standard-8 on-demand: $0.853624/h. e2-standard-4: $0.134023/h. Both read from the Cloud Billing Catalog API on 2026-10-19.
  - Cloud NAT: $0.0078/h.
  - Estimated extras: the GKE management fee (if the free tier doesn't cover it), the boot disks, and about 10 GiB of NAT data.
