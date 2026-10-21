#!/usr/bin/env bash
# P3-08: vLLM scales from zero on the gateway's traffic. Puts P3's scale-from-zero values on P1's vLLM
# release (0 replicas allowed; a KEDA trigger on the gateway's chat-default request rate), waits until no
# vLLM pod and no GPU node are left, then probes chat-default every 2 s (scale_from_zero.py): the hosted
# fallback answers while the GPU wakes, until vLLM answers itself. Leaves vLLM on these values.
# Nothing else may call chat-default meanwhile (stop the game day's load first), or vLLM never reaches 0.
# Usage (from the repo root, in a session with layers 01-03): ZONE=... [OUT=dir] scale_from_zero.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
p1="$here/../../01-gpu-serving"
out=${OUT:-$(mktemp -d)}
mkdir -p "$out"
# shellcheck source=/dev/null  # lib.sh, next to scale_from_zero.sh
source "$here/lib.sh"
trap stop_port_forwards EXIT

# The new values on top of the release's own (model config, registry, labels); KEDA must not be paused.
helm upgrade vllm "$p1/helm/vllm" -n vllm --reset-then-reuse-values -f "$here/../helm/vllm-scale-from-zero.yaml"
kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true

# wait_for SECONDS DESCRIPTION COMMAND...: until COMMAND prints nothing.
wait_for() {
  local limit=$1 what=$2; shift 2
  for _ in $(seq $((limit / 10))); do [ -z "$("$@")" ] && { echo "$(date -u +%H:%M:%SZ) $what"; return; }; sleep 10; done
  echo "timed out after ${limit}s waiting for: $what"; exit 1
}
# KEDA's cooldown (5 min) after the last chat-default request, then the idle GPU node goes.
wait_for 1200 "no vLLM pod" kubectl get pods -n vllm -l app.kubernetes.io/name=vllm -o name
wait_for 1800 "no GPU node" kubectl get nodes -l cloud.google.com/gke-nodepool=gpu -o name
# A fresh port-forward: the one lib.sh opened died when the GPU node went (session 2's retry, 2026-10-21), likely
# with the tunnel pod (konnectivity-agent) GKE removed alongside it.
stop_port_forwards
port_forward gateway/gateway 14300:4000

python3 "$here/scale_from_zero.py" | tee "$out/scale-from-zero.txt"
echo "probe log: $out/scale-from-zero.txt"
