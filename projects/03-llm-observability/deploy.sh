#!/usr/bin/env bash
# Deploy P3 into the running session cluster: P1's kube-prometheus-stack release upgraded with P3's
# values (Grafana, Alertmanager, kube-state-metrics), Tempo for traces, then P2's OTel Collector upgraded
# with P3's values (traces on to Tempo, span metrics for Prometheus). Needs P2 first. Works with or
# without P1: with `make up LAYERS="02 03"` it installs the Prometheus release itself, with P1's values.
# Usage (from the repo root): ZONE=us-central1-c projects/03-llm-observability/deploy.sh
set -euo pipefail
# Same settings as the Makefile: never the machine's default project or kubeconfig.
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
here=$(cd "$(dirname "$0")" && pwd)
p1="$here/../01-gpu-serving"
project=$(gcloud config get project 2>/dev/null)
owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
# The cluster's own session label, so Kubernetes objects match the Terraform resources.
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
[ -n "$owner" ] && [ -n "$session" ] || { echo "couldn't read the owner or session label"; exit 1; }
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }

# P1's namespaces (monitoring among them): without P1 the others stay empty.
kubectl apply -f "$p1/helm/namespaces.yaml"
kubectl label namespace monitoring owner="$owner" session="$session" --overwrite
# The same release name, namespace and P1 values: P1's KEDA reads prometheus-operated.monitoring.svc.
helm upgrade --install prometheus oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack --version 91.9.0 \
  -n monitoring -f "$p1/helm/prometheus.yaml" -f "$here/helm/monitoring.yaml" \
  --set-string commonLabels.owner="$owner" --set-string commonLabels.session="$session" \
  --set-string grafana.extraLabels.owner="$owner" --set-string grafana.extraLabels.session="$session" \
  --set-string alertmanager.alertmanagerSpec.podMetadata.labels.owner="$owner" \
  --set-string alertmanager.alertmanagerSpec.podMetadata.labels.session="$session" \
  --set-string kube-state-metrics.customLabels.owner="$owner" --set-string kube-state-metrics.customLabels.session="$session" \
  --wait --timeout 10m
helm upgrade --install tempo tempo --repo https://grafana-community.github.io/helm-charts --version 3.1.0 \
  -n monitoring -f "$here/helm/tempo.yaml" \
  --set-string extraLabels.owner="$owner" --set-string extraLabels.session="$session" \
  --set-string podLabels.owner="$owner" --set-string podLabels.session="$session" \
  --wait --timeout 5m
# P2's collector: the same release and labels as P2's deploy.sh, with P3's values on top.
helm upgrade --install otel-collector opentelemetry-collector --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
  --version 0.174.2 -n gateway -f "$here/../02-llm-gateway/helm/otel-collector.yaml" -f "$here/helm/otel-collector.yaml" \
  --set-string additionalLabels.owner="$owner" --set-string additionalLabels.session="$session" \
  --set-string podLabels.owner="$owner" --set-string podLabels.session="$session" \
  --wait --timeout 5m
