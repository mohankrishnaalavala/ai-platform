#!/usr/bin/env bash
# P3-09's game day (session 2, with layers 01-03 up): three experiments, each timed to its first alert.
#   2-flood            an open-loop flood of long prompts straight to vLLM (P1's bench Job, REQUEST_RATE)
#                      burns the latency SLO; through the gateway it would fall back to the hosted model
#   1-vllm-killed      vLLM is killed (SIGKILL) and held at 0 replicas for HOLD_MIN, as a crash loop would;
#                      fallback keeps users served, so the fallback alert has to catch it
#   3-hosted-blocked   with vLLM still down, a NetworkPolicy blocks the gateway's calls outside the cluster,
#                      so the fallback fails too and the availability SLO burns
# Then vLLM is restored and its recovery timed: experiment 1's side note (one restart on the same node).
# The flood runs first, the order the session plans. Before each experiment, Alertmanager must have no
# active alert, so every alert is a new notification. A low load (load.py) runs through the gateway the whole time.
# Each run records the timings that add to time to alert (settings.json).
# Usage (from the repo root): ZONE=... CONFIG=<served config> [OUT=dir] gameday.sh
# Knobs: FLOOD_RATE (requests/s, 1.0: about 1.6x what vLLM served in P1's FP8 rag runs), FLOOD_MIN (10),
# HOLD_MIN (15), BLOCK_MIN (10), RECORDER (namespace/service:port of the alert receiver), and EXPECT_* (the
# alert-name regex each experiment waits for).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
p1="$here/../../../01-gpu-serving"
out=${OUT:-$(mktemp -d)}
mkdir -p "$out"
: "${CONFIG:?set CONFIG to the model config vLLM serves}"
rate=${FLOOD_RATE:-1.0} flood_min=${FLOOD_MIN:-10} hold_min=${HOLD_MIN:-15} block_min=${BLOCK_MIN:-10}
recorder=${RECORDER:-monitoring/alert-recorder:8080}
# shellcheck source=/dev/null  # ../lib.sh
source "$here/../lib.sh"
session=$(gcloud container clusters describe ai-platform --location="$ZONE" --format='value(resourceLabels.session)')
owner=$(gcloud projects describe "$PROJECT" --format='value(labels.owner)')
port_forward monitoring/alertmanager-operated 19093:9093
port_forward "${recorder%:*}" "19094:${recorder##*:}"

load=
cleanup() {
  # Whatever happened: no block left, KEDA back in charge, no load.
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
quiet() {  # until Alertmanager has no active alert, so the next one is a new notification
  for _ in $(seq 120); do
    [ "$(curl -sf 'http://127.0.0.1:19093/api/v2/alerts?active=true&silenced=false&inhibited=false')" = "[]" ] && return
    sleep 10
  done
  say "alerts still active after 20 min; going on (this experiment's time to alert may be [X])"
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

python3 "$here/load.py" > "$out/load.txt" 2>&1 & load=$!
say "background load started; 2 min for the gateway's series to settle"
sleep 120

# 2-flood: the bench Job sends NUM_PROMPTS rag prompts (4,000 tokens in, 256 out) at FLOOD_RATE a second,
# open-loop; the experiment starts when its container does (the image may take a while to pull).
quiet
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
event 2-flood "${EXPECT_LATENCY:-latency}" "$(python3 -c "import datetime as d; print(int(d.datetime.fromisoformat('${started/Z/+00:00}').timestamp()))")"
wait $bench || say "the flood's bench.sh failed (see flood.txt); the experiment still ran"
say "flood over"

# 1-vllm-killed: KEDA paused at 0 so nothing restarts it, and the pod killed at once, not stopped gently.
quiet
event 1-vllm-killed "${EXPECT_FALLBACK:-fallback}"
kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas=0 --overwrite >/dev/null
kubectl delete pod -n vllm -l app.kubernetes.io/name=vllm --grace-period=0 --force >/dev/null 2>&1 || true
sleep $((hold_min * 60))

# 3-hosted-blocked: vLLM still down, and now the hosted model is out of reach too.
event 3-hosted-blocked "${EXPECT_AVAILABILITY:-availab|error}"
kubectl label --local -f "$here/block-hosted.yaml" owner="$owner" session="$session" -o yaml | kubectl apply -f - >/dev/null
sleep $((block_min * 60))

# Restore, and time vLLM's return (a new pod on the same node, the image and weights already there).
kubectl delete networkpolicy block-hosted-model -n gateway >/dev/null
restore=$(date +%s)
kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas- >/dev/null
for _ in $(seq 240); do
  kubectl get pods -n vllm -l app.kubernetes.io/name=vllm \
    -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q True && break
  sleep 5
done
echo "vLLM ready again $(( $(date +%s) - restore )) s after KEDA was unpaused (one restart on the same node)" | tee "$out/recovery.txt"

kill "$load" 2>/dev/null || true; load=
curl -sf http://127.0.0.1:19094/log > "$out/alerts.jsonl"
python3 "$here/time_to_alert.py" "$out/events.jsonl" "$out/alerts.jsonl" | tee "$out/time-to-alert.md"
say "game day done; files in $out"
