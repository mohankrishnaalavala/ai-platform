#!/usr/bin/env bash
# Static chart checks, no cluster needed (CI runs this on every pull request): lint and render the
# gateway chart and the pinned OTel Collector chart with our values, validate them against the
# Kubernetes 1.36 schemas, and fail on anything that would expose a service, leave a disk behind,
# or put a secret value in a chart.
set -euo pipefail
cd "$(dirname "$0")/.."

chart=helm/gateway
kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
# Schemas for the custom resources (SecretProviderClass), pinned to a commit.
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/d373c2da9702bc9509a004db83e57263fe3bdfc1/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
# Placeholders for what deploy.sh reads from the cloud.
common=(--set project=p --set labels.owner=o --set labels.session=20261018t0000z)
out=$(mktemp)
trap 'rm -f "$out"' EXIT

helm lint "$chart" "${common[@]}"
{ helm template gateway "$chart" -n gateway "${common[@]}"; echo ---
  helm template otel-collector opentelemetry-collector --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
    --version 0.174.2 -n gateway -f helm/otel-collector.yaml; echo ---
  cat helm/namespaces.yaml; } >> "$out"

docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 -schema-location default -schema-location "$crds" -summary - < "$out"
if grep -nE 'type: (LoadBalancer|NodePort|ExternalName)|kind: (Ingress|PersistentVolumeClaim|Secret)$|hostNetwork: true|hostPort: [0-9]|hostPath:' "$out"; then
  echo "a chart renders a public service, an ingress, host access, a disk (PVC) or a Kubernetes Secret"; exit 1
fi
# Secrets reach the pods only as Secret Manager files: no key-like value may be rendered.
if grep -nE 'sk-[A-Za-z0-9]{8,}|password: ' "$out"; then
  echo "a rendered manifest holds what looks like a secret value"; exit 1
fi
echo "charts OK"
