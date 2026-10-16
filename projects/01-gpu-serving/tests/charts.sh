#!/usr/bin/env bash
# Static chart checks, no cluster needed (CI runs this on every pull request): lint and render every
# variant, validate the result against the Kubernetes 1.36 schemas, and fail on anything that would
# expose a service outside the cluster or leave a disk behind.
set -euo pipefail
cd "$(dirname "$0")/.."

chart=helm/vllm
kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
# Schemas for the custom resources (ScaledObject, ServiceMonitor, Prometheus), pinned to a commit.
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/d373c2da9702bc9509a004db83e57263fe3bdfc1/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
# Placeholders for what deploy.sh reads from the cloud.
common=(--set modelsBucket=p-models --set labels.owner=o --set labels.session=20261016t0000z)
out=$(mktemp)

for config in ../../config/models/*.yaml; do
  helm lint "$chart" -f "$config" "${common[@]}" --set registry=r/p/images
  { helm template vllm "$chart" -n vllm -f "$config" "${common[@]}" --set registry=r/p/images; echo ---
    helm template vllm "$chart" -n vllm -f "$config" -f "$chart/values-baseline.yaml" "${common[@]}"; echo ---; } >> "$out"
done
# The pinned third-party charts with our values, as deploy.sh installs them. Their CRDs (resource
# type definitions, not workloads) are left out: kubeconform has no schema for them, and their
# field descriptions would trip the grep below.
{ helm template prometheus oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack --version 91.9.0 -n monitoring -f helm/prometheus.yaml --set crds.enabled=false; echo ---
  helm template keda keda --repo https://kedacore.github.io/charts --version 2.21.0 -n keda -f helm/keda.yaml --set crds.install=false; echo ---
  cat helm/namespaces.yaml; } >> "$out"

docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 -schema-location default -schema-location "$crds" -summary - < "$out"
if grep -nE 'type: (LoadBalancer|NodePort|ExternalName)|kind: (Ingress|PersistentVolumeClaim)|hostNetwork: true|hostPort: [0-9]|hostPath:' "$out"; then
  echo "a chart renders a public service, an ingress, host access or a disk (PVC)"; exit 1
fi
echo "charts OK"
