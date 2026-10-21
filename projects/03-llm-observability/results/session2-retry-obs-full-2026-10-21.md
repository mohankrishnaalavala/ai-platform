# P3 session 2, retry: obs-full on GKE with vLLM on an on-demand L4 (2026-10-21). Game day and screenshots passed; scale from zero failed

**Run:** GitHub Actions [39519329832](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39519329832), `gpu-session.yml` mode `obs-full`, cluster zone `us-central1-a`, from `main` at commit `65ef57d`. The first try was [`session2-obs-full-2026-10-20.md`](session2-obs-full-2026-10-20.md); PRs #59 and #60 changed things before this one (below).

**Hardware:**
- GKE (Google Kubernetes Engine) `v1.36.4-gke.1495002`, one `e2-standard-8` system node in `us-central1-a`
- one on-demand `g2-standard-8` GPU node with one NVIDIA L4, in `us-central1-b` (the flood's `flood.txt` records `gpu_zone=us-central1-b`, `client_zone=us-central1-a`)

**Software:** the same versions as the first try: P3's stack as in [`session1-obs-shakedown-2026-10-20.md`](session1-obs-shakedown-2026-10-20.md), plus vLLM `v0.30.0` serving Qwen2.5-7B-Instruct in FP8 (8-bit floating point weights, `config/models/qwen2.5-7b-fp8.yaml`).

**What changed since the first try:**
- PR #59: Grafana's memory limit 512 MiB (was 256 MiB), a 120 s timeout around each headless Chrome call, and every step gets at least 1 s before the deadline.
- PR #60:
  - Grafana's limit went to 1 GiB, and obs-full got a 195-min deadline and a 225-min job limit.
  - The game day's background load became open-loop: one request every 2 s, each on its own thread, whether or not the last one has answered.
  - The game day saves the cost rules at the flood's end.

**Method:** `make up LAYERS="01 02 03"` with the FP8 config, then these steps, in order:
1. `make test P=03` with `DASHBOARD_METRICS="vllm: DCGM_"`
2. `tests/screenshots.sh`
3. `tests/gameday/gameday.sh`
4. `tests/scale_from_zero.sh`

The raw files are in [`session2-retry-20261021t0007z/`](session2-retry-20261021t0007z/), copied from the run's upload to the results bucket. The upload step had already taken out the project ID and number, emails and `sk-` keys. Lines from the run's log (which GitHub keeps for 90 days), its step list and its diagnostics are saved in [`log-excerpts.txt`](session2-retry-20261021t0007z/log-excerpts.txt); numbers below without another file come from there.

## Results

**`make test`: passed (00:51:29Z to 00:52:53Z).** 7 tests in 76.938 s ([`log-excerpts.txt`](session2-retry-20261021t0007z/log-excerpts.txt)), including `test_every_metric_has_a_series` for every `vllm:` and `DCGM_` name. Gateway calls `[200, 200, 200]`.

**Screenshots: passed (00:52:53Z to 00:58:26Z).** All three dashboards, under the background load, before the game day:
- [`p3-cost.png`](session2-retry-20261021t0007z/screenshots/p3-cost.png) (70,292 bytes)
- [`p3-gateway.png`](session2-retry-20261021t0007z/screenshots/p3-gateway.png) (71,198 bytes)
- [`p3-model-serving.png`](session2-retry-20261021t0007z/screenshots/p3-model-serving.png) (84,719 bytes)

Notes on the screenshots:
- Chrome hit its 120 s timeout on `p3-gateway` (`Chrome exited with 124`, [`log-excerpts.txt`](session2-retry-20261021t0007z/log-excerpts.txt)). It had already written the file, and Grafana answered before and after.
- Grafana didn't restart. `kubectl top` read 520 MiB at the end of the run ([`log-excerpts.txt`](session2-retry-20261021t0007z/log-excerpts.txt)), above the old 512 MiB limit.
- With all-zero data, the error, fallback and KV cache panels drew their axes up to 10000%. A later PR fixes their axes to 0–100%.
- I checked all three by eye. They show no secrets, emails or project ID.

**Game day: passed (00:58:26Z to 02:13:45Z).** Each experiment raised the alert it waited for. The two burn-rate alerts come from the SLOs (service level objectives) in `slos/platform.yaml`. Time to alert runs from the experiment's start to when the alert receiver got the first matching alert ([`time-to-alert.md`](session2-retry-20261021t0007z/gameday/time-to-alert.md), [`alerts.jsonl`](session2-retry-20261021t0007z/gameday/alerts.jsonl)):

| Experiment | Started | Alert | First (ticket) | Page |
|---|---|---|---|---|
| 2: flood straight to vLLM (rag prompts, 1.0 request/s, open-loop, 30 min) | 01:01:01Z | `VllmTtftBudgetBurn` (time to first token, TTFT) | 01:06:40Z, **5.7 min** | 01:10:10Z, 9.2 min |
| 1: vLLM killed, held at 0 for 15 min | 01:40:40Z | `ChatDefaultFallbackHigh` | 01:52:11Z, **11.5 min** (page only; its rule has a 10-min `for:`) | |
| 3: hosted model blocked (vLLM still at 0) | 01:55:43Z | `GatewayAvailabilityBudgetBurn` | 01:58:02Z, **2.3 min** | 01:59:32Z, 3.8 min |

- **The cluster's timings** ([`settings.json`](session2-retry-20261021t0007z/gameday/settings.json)): scrape 15 s, rule evaluation 30 s, Alertmanager group wait 10 s and group interval 1 min, span-metrics flush 15 s.
- **Already firing at each start:**
  - None at experiment 2.
  - `VllmTtftBudgetBurn` at experiment 1.
  - `ChatDefaultFallbackHigh` and `VllmTtftBudgetBurn` at experiment 3.
- **`ChatDefaultFallbackHigh` also fired during the flood** (01:17:41Z), because the background requests fell back to the hosted model. It wasn't firing when experiment 1 started (`settings.json`, `firing_at_start`); the receiver got its resolved notice 1 s later (01:40:41Z).
- **Recovery** ([`recovery-one-kill.txt`](session2-retry-20261021t0007z/gameday/recovery-one-kill.txt), [`recovery.txt`](session2-retry-20261021t0007z/gameday/recovery.txt)):
  - One SIGKILL (an immediate kill) with KEDA (the autoscaler) in charge: a new vLLM pod was Ready 251 s after the kill, on the same node.
  - After experiment 3: vLLM was Ready 476 s after KEDA was unpaused. No GPU node was left, so this includes a cold node start.

**The background load through the gateway** ([`load.txt`](session2-retry-20261021t0007z/gameday/load.txt)): one request every 2 s, each line written when its request ended, counted here by that time.

| Window (UTC) | Requests | Status | Fell back to `chat-hosted` | Median latency |
|---|---|---|---|---|
| 00:58:44–01:01:01, warm-up | 68 | 68 × 200 | 0 | 247 ms |
| 01:01:01–01:31:11, experiment 2 | 890 | 890 × 200 | 742 | 30,570 ms |
| 01:31:11–01:40:40, one kill, then the wait for the fallback alert to clear | 300 | 300 × 200 | 141 | 269 ms |
| 01:40:40–01:55:43, experiment 1 | 451 | 451 × 200 | 450 | 577 ms |
| 01:55:43–02:05:47, experiment 3 | 272 | 270 × 500, 2 × 200 | 2 | 60,744 ms |
| 02:05:47–02:13:43, restore | 268 | 257 × 200, 11 × 500 | 257 | 600 ms |

In experiment 3 each request waited out `chat-hosted`'s 60 s timeout. The gateway returned 500. That status counts against the availability SLO, which counts 5xx responses.

**Cost per 1M output tokens (P3-05), at the flood's end with vLLM saturated** ([`cost.txt`](session2-retry-20261021t0007z/gameday/cost.txt), Prometheus queries at 01:31Z):

| Query | Value |
|---|---|
| `p3:self_hosted_usd_per_1m_output_tokens` | **$1.61** (1.6147810781561132) |
| `p3:hosted_usd_per_1m_output_tokens` | **$4.60** (4.600000000000003) |
| `sum(rate(vllm:generation_tokens_total[5m]))` | 155.09 output tokens/s |
| `sum(rate(llm_output_tokens_by_model_total{litellm_model_group="chat-hosted"}[5m]))` | 0.50 output tokens/s |

- **Self-hosted:** the GPU node's on-demand list price ($0.853624271/h) over vLLM's output tokens in the last 5 minutes. The system node and NAT (the cluster's outbound internet gateway) aren't counted.
  - The rule's value implies about 146.8 tokens/s at its last evaluation, up to 30 s before the live token-rate query read 155.09.
- **Hosted:** what LiteLLM charged for `chat-hosted`, over the output tokens it returned. The charge covers input and output tokens, from LiteLLM's price list.
  - The requests were the background load's tiny ones (at most 5 output tokens each: `max_tokens=5` in P2's `tests/client.py`). So this is the cost per 1M output tokens for that request shape, not Google's list price per output token.

**Scale from zero: failed (02:13:45Z to 02:23:19Z), no probes.**
- The script put P3's scale-from-zero values on vLLM's release (0 replicas allowed, KEDA's trigger on the gateway's `chat-default` request rate).
- vLLM scaled to 0 on its own: `02:20:51Z no vLLM pod`. Then the GPU node went: `02:22:49Z no GPU node`.
- The first request through the gateway's port-forward then failed: `portforward.go:476] "Error creating error stream" err="Timeout occurred" localPort=14300 remotePort=4000`. A Python traceback in `new_key` followed (`ConnectionResetError: [Errno 104] Connection reset by peer`), so the probe never started, and `scale-from-zero.txt` is empty.
- The port-forward was opened at 02:13Z, when the script started.
- The cluster's events show GKE scaling its `konnectivity-agent` tunnel pods from 2 to 1 as the GPU node went ([`log-excerpts.txt`](session2-retry-20261021t0007z/log-excerpts.txt)). That likely took the port-forward's tunnel with it. Not proven: the events don't say which agent carried it.
- So vLLM scaling from 0 to 1 on gateway traffic is still unmeasured.

## Timing and teardown
- `make up`: 00:07:52Z to 00:51:29Z.
- Steps: as above. The results upload ran 02:23:19Z to 02:23:26Z, then diagnostics and the GPU pool to 0.
- **Teardown:** `make down` ran 02:25:30Z to 02:36:25Z. The teardown job's `make status` at 02:36:35Z reported "nothing billable is running". A local `make status` afterwards (not in the run's log) said the same.
