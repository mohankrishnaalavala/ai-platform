# P3: LLM observability and SLOs

Traces, the right metrics, SLOs (service level objectives) with burn-rate alerts, and cost per team for the P2 gateway and P1's vLLM. A game day then breaks things on purpose to prove the alerts fire.

**Status:** in progress since 2026-10-19. No session has run yet, so nothing here is measured in the cloud.

![P3 components](../../docs/diagrams/03-llm-observability-components.svg)

## What it runs (so far)

- **Monitoring:**
  - **One release:** P1's kube-prometheus-stack release, upgraded with P3's values ([`helm/monitoring.yaml`](helm/monitoring.yaml)). It adds Grafana, Alertmanager and kube-state-metrics; P1's KEDA keeps reading the same Prometheus.
  - **No disks:** data lasts one session.
  - **The charts create no Secret** (the operator and Helm keep their own):
    - Grafana has no admin user. Anyone who reaches it is an anonymous viewer.
    - Alertmanager's config comes from an `AlertmanagerConfig` resource. Every alert goes to the alert receiver (below).
  - **Nothing here can read Secrets:** Grafana may read ConfigMaps in its own namespace only, and kube-state-metrics doesn't collect Secrets.
- **GPU metrics:** read from the NVIDIA DCGM exporter GKE already runs on GPU nodes, so P3 runs no exporter of its own. It's selected by pod name and an assumed port, because its labels aren't documented. Not proven yet: session 1 checks it.
- **Traces:** Tempo, a single binary with no disk: a scratch volume of at most 2 GiB goes with the pod ([`helm/tempo.yaml`](helm/tempo.yaml)). Grafana reads it as a second data source.
- **The collector:** P2's OpenTelemetry Collector, upgraded with P3's values ([`helm/otel-collector.yaml`](helm/otel-collector.yaml)).
  - It sends the gateway's traces on to Tempo.
  - Its span metrics connector turns traces into request, error and latency metrics, which Prometheus scrapes.
  - Labels: model group, team and status code only. The latency buckets include 2.5 s (the latency SLO's threshold) and chat-default's 10 s and 30 s timeouts.
  - Metrics refresh every 15 s, not the default 60 s, since that delay adds to every alert.
  - Running P2's `deploy.sh` again puts P2's collector values back. Run P3's after it.
- **SLOs (service level objectives) as code** ([`slos/platform.yaml`](slos/platform.yaml)), over a 28-day window. Sloth generates the burn-rate rules ([`slos/platform.rules.yaml`](slos/platform.rules.yaml)), and CI checks that the committed file is exactly Sloth's output.
  - **Availability:** 99.5% of teams' gateway requests return a non-5xx status. A 429 (budget or rate limit) is the gateway working, not an error.
  - **Latency:** 95% of vLLM's requests get their first token within 2.5 s, a bucket boundary in vLLM v0.30.0, so the ratio is exact. The metric's name (`vllm:time_to_first_token_seconds`) comes from vLLM's source: not proven yet; session 2 checks the series exists.
  - **Alerts:** each SLO pages when its error budget burns 13.44x too fast over 1 h and 5 min, or 5.6x over 6 h and 30 min. It opens a ticket at 2.8x over 1 day and 2 h, or 0.93x over 3 days and 6 h. These are Sloth's 28-day windows.
- **Fallback alert** ([`slos/fallback.rules.yaml`](slos/fallback.rules.yaml)): over 10% of chat-default attempts fail (and fall back to the hosted model) for 10 min, with at least 3 attempts a minute.
- **Predicted time-to-alert** (promtool unit tests in [`tests/rules-test.yaml`](tests/rules-test.yaml), at a 1-minute rule interval; the game day measures the real one). The cluster evaluates rules every 30 s, so add the 15 s scrape, the collector's 15 s flush, the rule interval and Alertmanager's 10 s group wait to these minutes:
  - The gateway failing every request: ticket the minute the first errors land, page a minute later.
  - Every first token over 2.5 s after an hour of normal traffic: ticket after 2 min, page after 23 min. The good hour dilutes the 6-hour window.
  - vLLM dead: the fallback alert after 10 min (its `for`).
  - Quiet at 1% errors (a ticket only, no page) and at 0.5% fallbacks.
- **Alert receiver** ([`alert-receiver/receiver.py`](alert-receiver/receiver.py)): Alertmanager's only receiver, one stdlib Python file. It records each alert and the time it arrived, and serves them at `GET /log`. Nothing is sent outside the cluster.
- **Alert timing settings** (all part of time-to-alert): rules are evaluated every 30 s. Alertmanager groups by alert name and severity, so each alert has its own group, with `group_wait` 10 s, `group_interval` 1 min and `repeat_interval` 4 h. No alert fires all the time: there is no Watchdog, and the promtool tests check every alert is quiet with healthy traffic and with none.
- **Dashboards** ([`dashboards/`](dashboards/)), JSON in Git that `deploy.sh` loads as ConfigMaps for Grafana:
  - **Model serving:** time to first token, inter-token latency, tokens per second, queue, KV cache, and GPU use and memory used.
    - The vLLM metric names come from vLLM's metrics code for v0.30.0, and the GPU names from GKE's managed DCGM list. Not proven yet: session 1 checks the GPU names and session 2 the vLLM ones, with [`tests/test_dashboard_metrics.py`](tests/test_dashboard_metrics.py).
  - **Gateway:** model tries by team and model, the 5xx share, the fallback share, and request duration.
  - **Cost:** dollars per team over the last 24 hours, spend per hour by model, and cost per 1M output tokens, self-hosted against hosted.
- **Cost per team** (P3-05), from the traces, so it needs no database access:
  - **Source:** the collector's `sum` connector (alpha) adds up each model try's cost (`gen_ai.cost.total_cost`, the price the spend logs use) and output tokens, per team and per model.
  - **One label per metric:** in 0.161.0 the connector counts a value once per label, so two labels would double it.
  - **Running totals:** a delta-to-cumulative processor keeps them, because the Prometheus exporter can't add up the connector's deltas.
  - **Recording rules** ([`slos/cost.rules.yaml`](slos/cost.rules.yaml), loaded like the SLO rules):
    - self-hosted cost per 1M output tokens = the GPU node's on-demand list price ($0.853624271/h, read 2026-10-17, used even when a session runs spot) over the tokens vLLM produced
      - its vLLM metric (`vllm:generation_tokens_total`) isn't proven yet: session 2 checks it with `tests/test_dashboard_metrics.py`
    - hosted = LiteLLM's charge for chat-hosted over its output tokens. LiteLLM's price list isn't checked against Google's page.
    - Each returns nothing when there were no output tokens.
- **Network:** nothing is public. Every Service is ClusterIP, reached by `kubectl port-forward`.
- **In CI on every pull request** (the gateway job, with the collector's and Tempo's configs rendered from their charts):
  - [`tests/test_metrics.py`](tests/test_metrics.py): a trace reaches Tempo, the span metrics reach Prometheus with only the chosen labels, and the scale-from-zero query returns a number.
  - [`tests/test_dashboards.py`](tests/test_dashboards.py): every gateway dashboard query returns data, and each of its selectors matches series on its own. Its file checks run in the lint job too, and `tests/charts.sh` has promtool parse every dashboard query.
  - [`tests/test_cost.py`](tests/test_cost.py): for every team, the cost from the traces equals the team's running spend in LiteLLM's team table, which budgets are checked against. `tests/rules.sh` also tests the cost rules' math on made-up series (`promtool test rules`).
    - It compares with the team table, not the spend logs. In 5 CI runs, LiteLLM wrote no spend-log row for a team key's streamed call, while the team's spend counted it. The cause isn't proven. The spend-log totals are printed for reference.
  - [`tests/test_spans.py`](tests/test_spans.py), the span contract: the span names and attributes LiteLLM v1.103.3 sends, which P3's metrics are built from.
  - **No model on the request span:** the request (SERVER) span has the route, the status code and the team.
  - **Attempt spans:** each attempt at a model has its own span, with the model group, the team, the cost on success and ERROR on failure.
  - **No fallback flag:** a fallback is a failed `chat-default` attempt followed by a successful `chat-hosted` one.

## Scale from zero and the game day (session 2; not run yet)

- **Scale from zero** ([`tests/scale_from_zero.sh`](tests/scale_from_zero.sh)):
  - It puts [`helm/vllm-scale-from-zero.yaml`](helm/vllm-scale-from-zero.yaml) on P1's vLLM release: 0 replicas allowed, and a KEDA trigger on the gateway's `chat-default` request rate. That's the same query CI checks.
  - It waits until no vLLM pod and no GPU node are left, then probes `chat-default` every 2 s.
  - It records how fast the first request is answered (by the hosted fallback) and how long until vLLM answers itself.
  - P1's own defaults stay unchanged: 1 replica, scaled on the queue.
- **Game day** ([`tests/gameday/gameday.sh`](tests/gameday/gameday.sh)): it runs before scale from zero. A low load (1 request every 2 s) runs through the gateway the whole time, and each experiment is timed to its first alert.
  - **Flood:** an open-loop flood of long prompts straight to vLLM, from P1's bench Job, stopped after 30 min. It burns the latency SLO.
    - Only 2 min of good traffic come before it. The SLO rules' tests put the latency page about 23 min into the flood after an hour of good traffic, so the warm-up stays short and 30 min leaves room.
  - **vLLM killed:** vLLM is killed and held at 0 replicas for 15 min. The fallback alert must catch it, because users are still served.
    - Side note, timed just before it: one SIGKILL with KEDA in charge, until a new pod on the same node is Ready. It expects no alert, because the fallback covers it.
  - **Hosted model blocked:** with vLLM still down, a NetworkPolicy ([`tests/gameday/block-hosted.yaml`](tests/gameday/block-hosted.yaml)) blocks the gateway's calls outside the cluster. The fallback fails too, and the availability SLO burns.
    - The policy drops packets rather than refusing them, so failure takes `chat-hosted`'s timeout: 10 s streaming, 60 s plain.
  - **Recovery:** vLLM is then restored, and its recovery is timed. After 15+ min at 0 replicas the autoscaler normally has removed the GPU node, so this time normally includes a cold node start. The run records the GPU node count to show whether it did.
- **Waiting between experiments:** each experiment first waits until the alert it expects is neither pending nor firing, for 10 min at most, so that alert fires anew.
  - Other alerts may still be active: the fallback alert keeps firing through experiment 3, since vLLM is still down.
  - `settings.json` records which alerts were firing as each experiment started, or "unknown" if Prometheus didn't answer.
  - Alertmanager re-sends an alert in the same group at most every `group_interval` (1 min here), so a time to alert can include up to that.
- **Output:** [`tests/gameday/time_to_alert.py`](tests/gameday/time_to_alert.py) reads the alert receiver's log and prints the time to alert per experiment. Each run records the scrape interval, the rule interval, each alert's `for:` and Alertmanager's `group_wait`, since all of them add to time to alert.

## Run it

```sh
make up LAYERS="01 02 03"   # or "02 03" without the GPU: P3 then installs the Prometheus release itself
kubectl port-forward -n monitoring svc/prometheus-grafana 13000:80   # Grafana on http://127.0.0.1:13000
make down && make status
```

## Enterprise profile (documented, not built)

In production on GKE (Google Kubernetes Engine), managed services would replace the in-cluster pieces:

- **Metrics:** Google Cloud Managed Service for Prometheus instead of the in-cluster Prometheus. It uses the same PromQL queries, rules and Grafana dashboards. Google stores the data and runs the backend, so long retention needs no disks and no pods. It's billed per sample ingested, so the label rules here (team and model only) keep the cost down too.
- **Traces:** Cloud Trace instead of Tempo. The OpenTelemetry Collector sends to it with its Google Cloud exporter, under the collector's own Workload Identity, so there's no key.
- **Trade-offs:**
  - Per-sample and per-span charges replace a node's fixed cost.
  - Traces leave the cluster, though no prompt text is in them (P2 turns message logging off).
  - Switching back to self-hosted means re-pointing the collector and Grafana.

This project keeps everything in the session cluster, so it costs nothing when the cluster is gone.
