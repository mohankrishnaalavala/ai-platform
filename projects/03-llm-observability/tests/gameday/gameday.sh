#!/usr/bin/env bash
# P3-09's game day (session 2, with layers 01-03 up, before scale_from_zero.sh): three experiments, each
# timed to its first alert.
#   2-flood            an open-loop flood of long prompts straight to vLLM (P1's bench Job, REQUEST_RATE)
#                      burns the latency SLO; through the gateway it would fall back to the hosted model
#   1-vllm-killed      vLLM is killed (SIGKILL) and held at 0 replicas for HOLD_MIN, as a crash loop would;
#                      fallback keeps users served, so the fallback alert has to catch it
#   3-hosted-blocked   with vLLM still down, a NetworkPolicy blocks the gateway's calls outside the cluster,
#                      so the fallback fails too (chat-hosted's timeout: 10 s streaming, 60 s plain) and the
#                      availability SLO burns
# Experiment 1's side note: before it, one SIGKILL with KEDA in charge, timed until a new vLLM pod on the
# same node is Ready (no alert expected: fallback covers it). After experiment 3, vLLM is restored and timed
# too; by then the GPU node is gone (held at 0 for 15+ min), so that time includes a cold node start.
# The flood runs first, the order the session plans. Before each experiment, the alert it waits for must be
# neither pending nor firing (10 min at most); other alerts may still be active, and settings.json records
# which fired at each start. A low load (load.py) runs through the gateway the whole time.
# Each run records the timings that add to time to alert (settings.json).
# Usage (from the repo root): ZONE=... CONFIG=<served config> [OUT=dir] gameday.sh
# Knobs: FLOOD_RATE (requests/s; 1.0 is about 1.6x what vLLM served in P1's FP8 rag runs), FLOOD_MIN (30),
# HOLD_MIN (15), BLOCK_MIN (10), RECORDER (namespace/service:port of the alert receiver), and EXPECT_*
# (the alert-name regex each experiment waits for; defaults are P3's alert names).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
p1="$here/../../../01-gpu-serving"
out=${OUT:-$(mktemp -d)}
mkdir -p "$out"
: "${CONFIG:?set CONFIG to the model config vLLM serves}"
rate=${FLOOD_RATE:-1.0} flood_min=${FLOOD_MIN:-30} hold_min=${HOLD_MIN:-15} block_min=${BLOCK_MIN:-10}
expect_latency=${EXPECT_LATENCY:-VllmTtftBudgetBurn} expect_fallback=${EXPECT_FALLBACK:-ChatDefaultFallbackHigh}
expect_availability=${EXPECT_AVAILABILITY:-GatewayAvailabilityBudgetBurn}
recorder=${RECORDER:-monitoring/alert-receiver:8080}
# shellcheck source=/dev/null  # ../lib.sh
source "$here/../lib.sh"
session=$(gcloud container clusters describe ai-platform --location="$ZONE" --format='value(resourceLabels.session)')
owner=$(gcloud projects describe "$PROJECT" --format='value(labels.owner)')
port_forward monitoring/prometheus-operated 19090:9090
port_forward "${recorder%:*}" "19094:${recorder##*:}"

load='' bench=''
stop_flood() {  # deleting the Job drops whatever the flood still had waiting in vLLM
  [ -z "$bench" ] || kill "$bench" 2>/dev/null || true
  bench=
  kubectl delete job bench -n vllm --ignore-not-found --wait=false >/dev/null || true
  kubectl delete configmap bench-scripts -n vllm --ignore-not-found >/dev/null || true
}
cleanup() {
  # Whatever happened: no flood, no block left, KEDA back in charge, no load.
  stop_flood
  kubectl delete networkpolicy block-hosted-model -n gateway --ignore-not-found >/dev/null || true
  kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
  [ -z "$load" ] || kill "$load" 2>/dev/null || true
  stop_port_forwards
}
trap cleanup EXIT

say() { echo "$(date -u +%H:%M:%SZ) $*"; }
event() {  # event NAME EXPECTS [UNIX_TIME]
  printf '{"experiment": "%s", "started_at": %s, "expects": "%s"}\n' "$1" "${3:-$(date +%s)}" "$2" >> "$out/events.jsonl"
  say "start $1 (waits for an alert matching /$2/)"
}
quiet() {  # quiet EXPERIMENT ALERTNAME: until that alert is neither pending nor firing (10 min at most), so it
  # fires anew; other alerts may stay active. Then note in settings.json which alerts fire as EXPERIMENT starts.
  local alerts='{}'
  for _ in $(seq 60); do
    alerts=$(curl -sf 'http://127.0.0.1:19090/api/v1/query?query=ALERTS' || echo '{}')
    python3 "$here/time_to_alert.py" clear "$2" <<< "$alerts" && break
    sleep 10
  done
  python3 "$here/time_to_alert.py" clear "$2" <<< "$alerts" || say "$2 still active after 10 min; going on (its time to alert may be [X])"
  python3 "$here/time_to_alert.py" record "$out/settings.json" "$1" <<< "$alerts"
}
vllm_ready() {  # a Ready vLLM pod (rollout status returns at once while KEDA holds it at 0)
  for _ in $(seq 240); do
    kubectl get pods -n vllm -l app.kubernetes.io/name=vllm \
      -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q True && return
    sleep 5
  done
  echo "no Ready vLLM pod after 20 min"; exit 1
}

# The timings that add to time to alert, as configured in this cluster.
python3 - "$out/settings.json" <<'EOF'
import json, subprocess, sys
def get(*a):
    return json.loads(subprocess.check_output(["kubectl", "get", *a, "-o", "json"]))["items"]
spec = get("prometheus", "-n", "monitoring")[0]["spec"]
rules = {r["alert"]: r.get("for", "0s") for p in get("prometheusrules", "-A")
         for g in p["spec"].get("groups", []) for r in g.get("rules", []) if "alert" in r}
configs = get("alertmanagerconfigs", "-n", "monitoring")
route = configs[0]["spec"].get("route", {}) if configs else {}
json.dump({"scrape_interval": spec.get("scrapeInterval", "30s (default)"),
           "rule_interval": spec.get("evaluationInterval", "30s (default)"),
           "group_wait": route.get("groupWait", "30s (default)"),
           "group_interval": route.get("groupInterval", "5m (default)"),
           "span_metrics_flush": "15s (helm/otel-collector.yaml)", "for": rules}, open(sys.argv[1], "w"), indent=1)
EOF
cat "$out/settings.json"

# Only 2 min of good traffic before the flood: an hour of it would push the latency page to about 23 min
# into the flood (the SLO rules' promtool tests).
vllm_ready
python3 "$here/load.py" > "$out/load.txt" 2>&1 & load=$!
say "background load started; 2 min for the gateway's series to settle"
sleep 120

# 2-flood: the bench Job sends rag prompts (4,000 tokens in, 256 out) at FLOOD_RATE a second, open-loop,
# for FLOOD_MIN; the experiment starts when its container does (the image may take a while to pull).
quiet 2-flood "$expect_latency"
n=$(python3 -c "print(round($rate * 60 * $flood_min))")
SCENARIOS=rag CONCURRENCY=$n REQUEST_RATE=$rate NUM_PROMPTS=$n RESULTS_PREFIX="p3/gameday/$session/flood" \
  bash "$p1/bench/bench.sh" > "$out/flood.txt" 2>&1 & bench=$!
started=
for _ in $(seq 180); do
  started=$(kubectl get pods -n vllm -l job-name=bench -o jsonpath='{.items[0].status.containerStatuses[0].state.running.startedAt}' 2>/dev/null || true)
  [ -n "$started" ] && break
  sleep 5
done
[ -n "$started" ] || { echo "the flood's bench Job never started"; exit 1; }
event 2-flood "$expect_latency" "$(python3 -c "import datetime as d; print(int(d.datetime.fromisoformat('${started/Z/+00:00}').timestamp()))")"
for _ in $(seq $((flood_min * 6))); do kill -0 "$bench" 2>/dev/null || break; sleep 10; done
stop_flood
say "flood over (stopped after ${flood_min} min; its own results aren't kept, the SLI comes from Prometheus)"

# Side note: one SIGKILL, KEDA still in charge: how long until a new pod on the same node is Ready.
vllm_ready
kill_vllm() { kubectl delete pod -n vllm -l app.kubernetes.io/name=vllm --grace-period=0 --force >/dev/null 2>&1 || true; }
killed=$(date +%s)
kill_vllm
sleep 15  # until the killed pod is gone, so the Ready pod found next is the new one
vllm_ready
echo "one SIGKILL: vLLM Ready again after $(( $(date +%s) - killed )) s (a new pod on the same node)" | tee "$out/recovery-one-kill.txt"

# 1-vllm-killed: the pod killed at once (SIGKILL), then KEDA paused at 0 so nothing brings it back.
quiet 1-vllm-killed "$expect_fallback"
event 1-vllm-killed "$expect_fallback"
kill_vllm
kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas=0 --overwrite >/dev/null
sleep $((hold_min * 60))

# 3-hosted-blocked: vLLM still down, and now the hosted model is out of reach too. The fallback alert is
# expected to keep firing through it.
quiet 3-hosted-blocked "$expect_availability"
event 3-hosted-blocked "$expect_availability"
kubectl label --local -f "$here/block-hosted.yaml" owner="$owner" session="$session" -o yaml | kubectl apply -f - >/dev/null
sleep $((block_min * 60))

# Restore, and time vLLM's return: after 15+ min at 0 the autoscaler has removed the GPU node, so this
# includes a cold node start (P1's optimized cold start: median 402 s).
kubectl delete networkpolicy block-hosted-model -n gateway >/dev/null
restore=$(date +%s)
kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas- >/dev/null
vllm_ready
echo "restore: vLLM Ready again $(( $(date +%s) - restore )) s after KEDA was unpaused (includes a cold GPU node start)" | tee "$out/recovery.txt"

kill "$load" 2>/dev/null || true; load=
curl -sf http://127.0.0.1:19094/log > "$out/alerts.jsonl"
python3 "$here/time_to_alert.py" "$out/events.jsonl" "$out/alerts.jsonl" | tee "$out/time-to-alert.md"
say "game day done; files in $out (scale_from_zero.sh comes next)"
