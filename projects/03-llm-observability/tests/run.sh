#!/usr/bin/env bash
# P3 checks against the running session (`make up LAYERS="02 03"`, or "01 02 03"): test_session.py through
# port-forwards to Grafana, Prometheus, Tempo, the gateway and (when deployed) the alert receiver. The
# gateway's master key is read into memory only, as in P2's run.sh.
# `run.sh gpu-start` (obs-shakedown): a pause pod asks for one GPU, so GKE starts a GPU node, which runs
# GKE's DCGM exporter. `run.sh gpu-check` then waits for GPU metrics in Prometheus and deletes the pod
# (the workflow sets the GPU pool to 0 nodes after it).
# `run.sh fallback-alert` (obs-shakedown, no vLLM): chat-default calls until the fallback alert reaches
# the receiver, with the time it took (test_session.FallbackAlertTest). Skipped until the receiver exists.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"  # `python3 -m unittest <module>` imports from the current directory; make runs from the root
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
pids=()
trap 'kill ${pids[@]+"${pids[@]}"} 2>/dev/null || true' EXIT
# Unusual local ports, so a stray local server can't answer instead (P2's run.sh uses 141xx and 181xx).
forward() { kubectl port-forward -n "$1" "svc/$2" "$3" >/dev/null & pids+=($!); }
export GATEWAY_URL=http://127.0.0.1:14300 GRAFANA_URL=http://127.0.0.1:13000 PROMETHEUS_URL=http://127.0.0.1:19090 \
  TEMPO_URL=http://127.0.0.1:13200
receiver=alert-receiver  # P3-06's webhook recorder: alert-receiver.monitoring:8080, GET /log (one JSON line per alert)

if [ "${1:-}" = gpu-start ]; then
  project=$(gcloud config get project 2>/dev/null)
  owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
  session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
  # The GPU pool (not gpu-baseline) by name, as P1's vLLM does. GKE adds no workload of its own here
  # beyond the driver and the DCGM exporter.
  kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: gpu-check
  namespace: monitoring
  labels: {app: ai-platform, layer: "03", owner: "$owner", session: "$session"}
spec:
  nodeSelector: {cloud.google.com/gke-nodepool: gpu}
  tolerations: [{key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}]
  containers:
    - name: pause
      image: registry.k8s.io/pause:3.10@sha256:ee6521f290b2168b6e0935a181d4cff9be1ac3f505666ef0e3c98fae8199917a
      resources:
        requests: {cpu: 10m, memory: 16Mi}
        limits: {memory: 16Mi, nvidia.com/gpu: 1}
EOF
  exit
fi

if [ "${1:-}" = gpu-check ]; then
  trap 'kubectl delete pod gpu-check -n monitoring --ignore-not-found --wait=false; kill ${pids[@]+"${pids[@]}"} 2>/dev/null || true' EXIT
  kubectl wait --for=condition=Ready pod/gpu-check -n monitoring --timeout=15m || {
    echo "FAIL: no GPU node for the check (a spot stockout or the quota shows in these events)"
    kubectl get events -n monitoring --sort-by=.lastTimestamp | tail -20; exit 1; }
  forward monitoring prometheus-kube-prometheus-prometheus 19090:9090
  if python3 -c '
from test_metrics import query, until
found = until(lambda: query("DCGM_FI_DEV_GPU_UTIL"), 300)
print(f"GPU metrics from GKE DCGM exporter: {len(found or [])} DCGM_FI_DEV_GPU_UTIL series")
raise SystemExit(0 if found else 1)'; then
    echo "PASS: GPU metrics in Prometheus"
  else
    echo "FAIL: no GPU metrics in Prometheus (the diagnostics keep the exporter's pod spec and NetworkPolicies)"; exit 1
  fi
  exit
fi

forward gateway gateway 14300:4000
forward monitoring prometheus-grafana 13000:80
forward monitoring prometheus-kube-prometheus-prometheus 19090:9090
forward monitoring tempo 13200:3200
if kubectl get service "$receiver" -n monitoring >/dev/null 2>&1; then
  forward monitoring "$receiver" 18300:8080
  export RECEIVER_URL=http://127.0.0.1:18300
fi
for _ in $(seq 30); do
  curl -sf "$GATEWAY_URL/health/liveliness" >/dev/null && curl -sf "$GRAFANA_URL/api/health" >/dev/null && break
  sleep 1
done

project=$(gcloud config get project 2>/dev/null)
impersonate=()
[ -n "${GITHUB_ACTIONS:-}" ] || impersonate=(--impersonate-service-account="deployer@$project.iam.gserviceaccount.com")
master=$(gcloud secrets versions access latest --secret=litellm-master-key ${impersonate[@]+"${impersonate[@]}"} 2>/dev/null) || true
[ -n "$master" ] || { echo "FAIL: couldn't read the gateway's master key"; exit 1; }
[ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$master"
export MASTER_KEY="$master"
kubectl get deployment vllm -n vllm >/dev/null 2>&1 && export VLLM_DEPLOYED=1

if [ "${1:-}" = fallback-alert ]; then
  [ -n "${RECEIVER_URL:-}" ] || { echo "SKIP: fallback alert: no alert receiver deployed yet (P3-06 adds it)"; exit 0; }
  [ -z "${VLLM_DEPLOYED:-}" ] || { echo "FAIL: vLLM is deployed, so chat-default won't fall back"; exit 1; }
  FALLBACK_ALERT=1 python3 -m unittest -v test_session.FallbackAlertTest
  exit
fi

if python3 -m unittest -v test_session.SessionTest; then echo "PASS: P3 session checks"; else echo "FAIL: P3 session checks"; exit 1; fi
