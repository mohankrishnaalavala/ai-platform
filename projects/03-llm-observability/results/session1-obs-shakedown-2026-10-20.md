# P3 session 1: observability shakedown on GKE, a spot GPU only for the GPU check (2026-10-20)

**Run:** GitHub Actions [39453261330](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39453261330), `gpu-session.yml` mode `obs-shakedown`, zone `us-central1-a`, from `main` at commit `2d6e930`.

**Earlier tries (same mode, no results):**
- Try 1 ([39443457813](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39443457813), `us-central1-c`): GKE couldn't get the machines in `us-central1-c` (a Google Compute Engine stockout). Nothing was measured.
- Try 2 ([39444726909](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39444726909), `us-central1-a`): `make up` failed. Grafana's datasource init container ran in WATCH mode and never exited, so Grafana never became ready. Fixed in PR #57 (`watchMethod: LIST`, and `tests/charts.sh` now fails on a WATCH init container).

**Hardware:**
- GKE `v1.36.4-gke.1495002`, one `e2-standard-8` system node
- For the GPU check only: one spot `g2-standard-8` node with one NVIDIA L4. The pool was then resized to 0 nodes, and the node list in the diagnostics (16:49Z) shows only the system node.

**Software:**
- kube-prometheus-stack chart `91.9.0` (operator `v0.94.1`), one release with P1's and P3's values
- Tempo chart `3.1.0` (Tempo `3.1.0`)
- OTel Collector chart `0.174.2` (contrib `0.161.0`), P2's release with P3's values
- LiteLLM `v1.103.3` (the running pod's image digest matches P2's pinned tag), with P2's gateway chart `0.1.0`
- GKE's managed NVIDIA DCGM exporter on the GPU node
- No vLLM in this session (`LAYERS="02 03"`), so `chat-default` had no backend and fell back to `chat-hosted`.

**Method:**
1. `make up LAYERS="02 03"`, then `make test P=03` (`tests/run.sh`, `test_session.SessionTest`) through port-forwards.
2. `tests/run.sh gpu-check`: a pause pod asks for one GPU, so GKE starts a GPU node and its DCGM exporter. The check waits up to 5 min for `DCGM_FI_DEV_GPU_UTIL` in Prometheus, then deletes the pod.
3. `tests/run.sh fallback-alert` (`test_session.FallbackAlertTest`): one gateway request to `chat-default` every 2 s. With no vLLM, every request falls back, so the fallback share is 100%. The time to alert is from the first request until the alert receiver logs `ChatDefaultFallbackHigh`.

The lines below are copied from the run's log, which GitHub keeps for 90 days.

## Results
```text
test_alert_receiver_answers ... ok
test_dashboards_from_git_are_loaded ... ok
test_gateway_calls_reach_tempo_and_span_metrics ... ok
test_grafana_answers ... ok
test_rules_are_loaded ... ok
test_scrape_targets_are_up ... ok
gateway calls as p3-session-13455269: [200, 200, 200]
Ran 6 tests in 91.532s
OK
PASS: P3 session checks

pod/gpu-check created                                     16:25:32Z
pod/gpu-check condition met                               16:36:46Z
GPU metrics from GKE DCGM exporter: 1 DCGM_FI_DEV_GPU_UTIL series
PASS: GPU metrics in Prometheus

test_the_fallback_alert_fires ... ok
fallback alert: reached the receiver 672 s after the first request (255 requests, one every 2 s); alertname ChatDefaultFallbackHigh, startsAt 2026-10-20T16:49:06.3Z
Ran 1 test in 676.863s
OK
```

**What this proves:**
- **Monitoring stack (P3-01):** Prometheus, Alertmanager, Grafana, kube-state-metrics, Tempo and the alert receiver all ran on GKE, and every scrape target was up.
- **Traces and span metrics (P3-02):** gateway calls reached Tempo as traces and Prometheus as span metrics.
- **GPU metrics (P3-03):** the PodMonitor (pods named `dcgm-exporter-*`, port 9400) scraped GKE's DCGM exporter. The pod name and the port were assumptions until now. Only `DCGM_FI_DEV_GPU_UTIL` was checked. The dashboard's other GPU metric, `DCGM_FI_DEV_FB_USED`, is checked in session 2.
- **Dashboards:** all three loaded from Git on a fresh `make up`.
- **Rules:** the SLO, fallback and cost rule files loaded into Prometheus. Their burn-rate alerts didn't fire here; that's the game day's job (session 2).
- **Fallback alert (P3-07), end to end:** rule → Alertmanager → `AlertmanagerConfig` route → alert receiver. The time to alert was 672 s (11.2 min) at a 100% fallback share. Most of that is the rule's 10-minute `for:`. The test's limit was 15 min.

## Resource use (`kubectl top`, after the tests)
| Pod | CPU | Memory |
|---|---|---|
| litellm | 34m | 572Mi |
| presidio-analyzer | 3m | 735Mi |
| prometheus-grafana | 49m | 315Mi |
| tempo | 8m | 139Mi |
| postgres | 19m | 79Mi |
| prometheus | 9m | 74Mi |
| otel-collector | 5m | 49Mi |
| presidio-anonymizer | 1m | 41Mi |
| alertmanager | 1m | 19Mi |
| kube-state-metrics | 7m | 19Mi |
| prometheus-operator | 4m | 18Mi |
| alert-receiver | 1m | 13Mi |

**Node:** requests were 3066m CPU (38%) and 8.15 GB memory (27%) of the `e2-standard-8`. Limits were 164% of CPU and 54% of memory.

## Timing and teardown
- **Timing:**
  - `make up`: 15:59:18Z to 16:23:53Z
  - `make test`: 16:23:53Z to 16:25:29Z
  - GPU check: 16:25:29Z to 16:36:53Z. Most of it was the GPU node starting; the pod was ready at 16:36:46Z.
  - GPU pool to 0 nodes: 16:36:53Z to 16:37:57Z
  - fallback alert: 16:37:57Z to 16:49:20Z
- **Teardown:** `make down` ran 16:50:37Z to 17:00:46Z. The teardown job's `make status` at 17:00:55Z reported "nothing billable is running", A local `make status` run afterwards (not in the run's log) said the same.
