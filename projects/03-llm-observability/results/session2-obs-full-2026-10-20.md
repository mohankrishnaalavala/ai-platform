# P3 session 2: obs-full on GKE with vLLM on an on-demand L4 (2026-10-20). **Failed:** no game day or scale-from-zero numbers

**Run:** GitHub Actions [39464853760](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39464853760), `gpu-session.yml` mode `obs-full`, zone `us-central1-a`, from `main` at commit `2d2187c`.

**Hardware:**
- GKE `v1.36.4-gke.1495002`, one `e2-standard-8` system node
- one on-demand `g2-standard-8` GPU node with one NVIDIA L4

**Software:**
- the same versions as session 1 ([`session1-obs-shakedown-2026-10-20.md`](session1-obs-shakedown-2026-10-20.md))
- plus vLLM `v0.30.0` serving Qwen2.5-7B-Instruct FP8 (`config/models/qwen2.5-7b-fp8.yaml`)

**Method:** `make up LAYERS="01 02 03"` with the FP8 config, then these steps, in order:
1. `make test P=03` with `DASHBOARD_METRICS="vllm: DCGM_"`
2. `tests/screenshots.sh`
3. `tests/gameday/gameday.sh`
4. `tests/scale_from_zero.sh`

Each step was cut off at the job's measurement deadline, 150 minutes after the job started (19:48:09Z). The log lines below are copied from the run's log, which GitHub keeps for 90 days. The other files come from the run's upload to the results bucket and its diagnostics artifact.

## Results

**`make test`: passed (18:00:28Z to 18:01:15Z).**
```text
test_alert_receiver_answers ... ok
test_dashboards_from_git_are_loaded ... ok
test_gateway_calls_reach_tempo_and_span_metrics ... ok
test_grafana_answers ... ok
test_rules_are_loaded ... ok
test_scrape_targets_are_up ... ok
test_every_metric_has_a_series (test_dashboard_metrics.DashboardMetricsTest.test_every_metric_has_a_series) ... ok
gateway calls as p3-session-19242077: [200, 200, 200]
Ran 7 tests in 40.400s
OK
```
This proves the rest of the dashboards' metric names. Every `vllm:` and `DCGM_` name in the dashboards and rule files had a series in Prometheus:
- `DCGM_FI_DEV_FB_USED` and `DCGM_FI_DEV_GPU_UTIL`
- `vllm:generation_tokens_total`
- `vllm:inter_token_latency_seconds_bucket`
- `vllm:kv_cache_usage_perc`
- `vllm:num_requests_running` and `vllm:num_requests_waiting`
- `vllm:time_to_first_token_seconds_bucket` and `vllm:time_to_first_token_seconds_count`

**Screenshots: failed (18:01:15Z to 19:48:09Z, cut off at the deadline).**
- After the 3-minute warm-up, headless Chrome opened the first dashboard (`p3-cost`). Grafana was then OOMKilled (exit 137) at 18:04:50Z under its 256 MiB memory limit. Its container restarted at 18:04:51Z.
- The port-forward to Grafana ended: `error: lost connection to pod`.
- The saved `p3-cost.png` shows only "Failed to load dashboard / Failed to fetch". It isn't usable.
- That Chrome process never exited, and the script had no timeout around it. So the step ran until the deadline killed it (exit 124).

**Game day: no results (19:48:09Z to 20:18:19Z, cancelled).**
- It started in the same second as the deadline, so the step got `timeout -k 30 0`. GNU `timeout 0` means no limit, so it ran on until the job's 180-minute limit cancelled it.
- It started background load at 19:48:22Z and the flood (experiment 2) at 19:50:41Z. It was cancelled during the flood's 30 minutes. The flood experiment reads its time to alert from the alert receiver after the flood ends, so **no time to alert was recorded, and whether `VllmTtftBudgetBurn` fired is unknown.** Experiments 1 and 3 didn't run.
- What the flood did, from the gateway's background load (`load.txt`: one closed-loop request at a time through the gateway, about one every 2 s):

  | Minutes (UTC) | Requests | Over 2.5 s | Fell back to `chat-hosted` |
  |---|---|---|---|
  | 19:48–19:49 | 48 | 0 | 0 |
  | 19:50–19:59 | 168 | 14 | 8 |
  | 20:00–20:09 | 19 | 19 | 19 |
  | 20:10–20:18 | 17 | 17 | 17 |

  All 252 returned 200. From 20:00 on, every request took about 30.6 s and fell back to the hosted model, which fits `chat-default`'s 30 s timeout.
- vLLM's own log (the last 300 lines, 20:14Z to 20:18Z): `Running: 44 reqs, Waiting: 514 reqs, GPU KV cache usage: 98.6%` at 20:14:53Z, rising to `Running: 45 reqs, Waiting: 565 reqs, GPU KV cache usage: 99.9%` at 20:17:53Z. Prometheus kept scraping `/metrics` (200 OK).

**Scale from zero: didn't run.**

## Fixes (the PR after this run)
- Grafana's memory limit goes to 512 MiB.
- `screenshots.sh` puts a 120 s timeout around each Chrome call, and checks Grafana's health before and after each screenshot.
- `gpu-session.yml` gives each step at least 1 second instead of 0 (a `left` helper). A step that starts after the deadline now fails at once.
- The diagnostics now save the alert receiver's log, so a run that is cut off keeps its alert times.

## Timing and teardown
- **Timing:**
  - `make up`: 17:19:04Z to 18:00:28Z. The vLLM rollout finished at 17:55:15Z.
  - The GPU pool was resized to 0 at 20:18:56Z to 20:20:17Z.
- **Teardown:** `make down` ran 20:21:12Z to 20:31:40Z. The teardown job's `make status` at 20:31:48Z reported "nothing billable is running". A local `make status` afterwards (not in the run's log) said the same.
