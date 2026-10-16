#!/usr/bin/env bash
# Static chart checks, no cluster needed (CI runs this on every pull request): lint and render every
# variant, validate the result against the Kubernetes 1.36 schemas, and fail on anything that would
# expose a service outside the cluster or leave a disk behind.
set -euo pipefail
cd "$(dirname "$0")/.."

chart=helm/vllm
kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
# Placeholders for what deploy.sh reads from the cloud.
common=(--set modelsBucket=p-models --set labels.owner=o --set labels.session=20261016t0000z)
out=$(mktemp)

for config in ../../config/models/*.yaml; do
  helm lint "$chart" -f "$config" "${common[@]}" --set registry=r/p/images
  { helm template vllm "$chart" -n vllm -f "$config" "${common[@]}" --set registry=r/p/images; echo ---
    helm template vllm "$chart" -n vllm -f "$config" -f "$chart/values-baseline.yaml" "${common[@]}"; echo ---; } >> "$out"
done
cat helm/namespaces.yaml >> "$out"

docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 -summary - < "$out"
if grep -nE 'type: (LoadBalancer|NodePort|ExternalName)|kind: (Ingress|PersistentVolumeClaim)|host(Port|Network|Path):' "$out"; then
  echo "a chart renders a public service, an ingress, host access or a disk (PVC)"; exit 1
fi
echo "charts OK"
