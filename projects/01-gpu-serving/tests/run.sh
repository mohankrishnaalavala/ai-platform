#!/usr/bin/env bash
# P1 checks against the running session (`make up LAYERS=01` first): the smoke tests through a
# port-forward, then the autoscaling path (KEDA's metrics API, the ScaledObject, Prometheus' scrape).
# Read-only.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
fail=0
check() { if [ -n "$2" ]; then echo "FAIL: $1: $2"; fail=1; else echo "PASS: $1"; fi; }

# An unusual local port, so a stray local server can't answer instead of vLLM.
kubectl port-forward -n vllm svc/vllm 18000:8000 >/dev/null &
pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 30); do curl -sf http://localhost:18000/health >/dev/null && break; sleep 1; done
kill -0 $pf 2>/dev/null || { echo "FAIL: the port-forward to vLLM died"; exit 1; }
if VLLM_URL=http://localhost:18000 python3 -m unittest discover -s "$here" -p 'test_smoke.py' -v; then
  echo "PASS: smoke tests"
else
  echo "FAIL: smoke tests"; fail=1
fi

api=$(kubectl get apiservice v1beta1.external.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' || true)
check "KEDA's metrics API is available" "$([ "$api" = True ] || echo "Available=${api:-missing}")"
ready=$(kubectl get scaledobject vllm -n vllm -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' || true)
check "the ScaledObject is ready" "$([ "$ready" = True ] || echo "Ready=${ready:-missing}")"
metric=$(kubectl get --raw "/apis/external.metrics.k8s.io/v1beta1/namespaces/vllm/s0-prometheus?labelSelector=scaledobject.keda.sh%2Fname%3Dvllm" 2>&1 || true)
check "KEDA reads the queue length from Prometheus" "$(jq -e '.items[0].value' <<< "$metric" >/dev/null 2>&1 || echo "no value: ${metric:0:200}")"
# Through the pod, not the API server's service proxy: the control plane can't reach port 9090.
up=$(kubectl exec -n monitoring statefulset/prometheus-prometheus-kube-prometheus-prometheus -c prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=up%7Bnamespace%3D%22vllm%22%7D' 2>/dev/null | jq -r '.data.result[].value[1]' | sort -u || true)
check "Prometheus scrapes vLLM" "$([ "$up" = 1 ] || echo "up=${up:-no target}")"

if [ $fail -ne 0 ]; then echo "some P1 checks failed"; exit 1; fi
echo "all P1 checks passed"
