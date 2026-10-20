#!/usr/bin/env bash
# Static chart checks, no cluster needed (CI runs this on every pull request): render P1's
# kube-prometheus-stack release with P3's values on top, and the Tempo chart, as deploy.sh installs them.
# Validate them against the Kubernetes 1.36 schemas, and fail on anything that would expose a service,
# leave a disk behind, or put a Kubernetes Secret or a secret value in a chart.
set -euo pipefail
cd "$(dirname "$0")/.."

kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
# Schemas for the custom resources (Prometheus, Alertmanager, AlertmanagerConfig, ServiceMonitor), pinned to a commit.
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/d373c2da9702bc9509a004db83e57263fe3bdfc1/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
out=$(mktemp)
trap 'rm -f "$out"' EXIT

# CRDs are left out, as in P1's checks: kubeconform has no schema for them, and their field
# descriptions would trip the grep below. owner and session are placeholders for what deploy.sh reads.
{ helm template prometheus oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack --version 91.9.0 -n monitoring \
    -f ../01-gpu-serving/helm/prometheus.yaml -f helm/monitoring.yaml --set crds.enabled=false \
    --set-string commonLabels.owner=o --set-string commonLabels.session=20261020t0000z; echo ---
  helm template tempo tempo --repo https://grafana-community.github.io/helm-charts --version 3.1.0 -n monitoring \
    -f helm/tempo.yaml; } >> "$out"

docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 -schema-location default -schema-location "$crds" -summary - < "$out"
if grep -nE 'type: (LoadBalancer|NodePort|ExternalName)|kind: (Ingress|PersistentVolumeClaim|Secret)$|hostNetwork: true|hostPort: [0-9]|hostPath:' "$out"; then
  echo "a chart renders a public service, an ingress, host access, a disk (PVC) or a Kubernetes Secret"; exit 1
fi
if grep -nE 'sk-[A-Za-z0-9]{8,}|password: ' "$out"; then
  echo "a rendered manifest holds what looks like a secret value"; exit 1
fi
echo "charts OK"
