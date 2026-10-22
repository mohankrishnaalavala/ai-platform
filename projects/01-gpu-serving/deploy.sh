#!/usr/bin/env bash
# Deploy P1 into the running session cluster: namespaces, Prometheus, KEDA, then vLLM with one
# model config. Prometheus and KEDA images come through Cloud NAT, pinned by chart version.
# Usage (from the repo root):
#   ZONE=us-central1-c [CONFIG=config/models/qwen2.5-7b-fp8.yaml] [VARIANT=baseline] [VLLM_ONLY=1] projects/01-gpu-serving/deploy.sh
# Defaults: the BF16 config and the optimized path (Artifact Registry + weights from Cloud Storage).
# VLLM_ONLY=1 swaps vLLM's model on a running session and touches nothing else (P4's offline evals:
# CONFIG=… VLLM_ONLY=1 make deploy LAYERS=01). Without it, the Prometheus step puts back P1's values alone,
# which removes P3's Grafana, Alertmanager and kube-state-metrics and restarts Prometheus, losing its data.
set -euo pipefail
# Same settings as the Makefile: never the machine's default project or kubeconfig.
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}

here=$(cd "$(dirname "$0")" && pwd)
config=${CONFIG:-config/models/qwen2.5-7b-bf16.yaml}
project=$(gcloud config get project 2>/dev/null)
owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
# The cluster's own session label, so Kubernetes objects match the Terraform resources.
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
[ -n "$owner" ] && [ -n "$session" ] || { echo "couldn't read the owner or session label"; exit 1; }
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }

if [ -z "${VLLM_ONLY:-}" ]; then
  kubectl apply -f "$here/helm/namespaces.yaml"
  kubectl label namespace vllm monitoring keda owner="$owner" session="$session" --overwrite

  # KEDA's ScaledObject (in the vLLM chart) needs both running first.
  helm upgrade --install prometheus oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack --version 91.9.0 \
    -n monitoring -f "$here/helm/prometheus.yaml" --set-string commonLabels.owner="$owner" --set-string commonLabels.session="$session" \
    --wait --timeout 10m
  helm upgrade --install keda keda --repo https://kedacore.github.io/charts --version 2.21.0 \
    -n keda -f "$here/helm/keda.yaml" --set-string additionalLabels.owner="$owner" --set-string additionalLabels.session="$session" \
    --wait --timeout 10m
fi

# vLLM's values come fresh from CONFIG every time (no --reuse-values), so nothing carries over from the last
# model, e.g. Qwen3-8B's thinking-off setting. The chart's Recreate strategy stops the old pod first.
args=(-f "$config" --set modelsBucket="$project-models" --set-string labels.owner="$owner" --set-string labels.session="$session")
case "${VARIANT:-optimized}" in
  optimized) args+=(--set registry="us-central1-docker.pkg.dev/$project/images") ;;
  baseline) args+=(-f "$here/helm/vllm/values-baseline.yaml") ;;
  *) echo "VARIANT must be optimized or baseline"; exit 1 ;;
esac
helm upgrade --install vllm "$here/helm/vllm" -n vllm "${args[@]}"
# A cold GPU node, the image and 15 GB of weights: this can take a while.
kubectl rollout status deployment/vllm -n vllm --timeout=40m || {
  # Say why: a GPU stockout or quota shows in the pod's events (FailedScaleUp), not in the rollout.
  kubectl get events -n vllm --sort-by=.lastTimestamp | tail -30
  exit 1
}
