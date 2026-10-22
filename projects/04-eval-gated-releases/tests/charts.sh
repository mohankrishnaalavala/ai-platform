#!/usr/bin/env bash
# Static checks for what P4 puts in the cluster, no cluster needed (CI runs every projects/*/tests/charts.sh):
# P2's gateway chart with P4's values, and the probe Job promote.py creates, against the Kubernetes 1.36
# schemas. Fails on anything public, any host access or a disk, and if P4's values don't reach the config.
set -euo pipefail
cd "$(dirname "$0")/.."

kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/d373c2da9702bc9509a004db83e57263fe3bdfc1/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
out=$(mktemp)
trap 'rm -f "$out"' EXIT

{ helm template gateway ../02-llm-gateway/helm/gateway -n gateway -f helm/gateway-canary.yaml \
    --set project=p --set labels.owner=o --set labels.session=20261018t0000z
  # Each object as its own JSON document (valid YAML), with a placeholder key.
  python3 -c 'import json, sys; sys.path.insert(0, "scripts"); import promote
for o in promote.probe_manifest("placeholder", 20, "o", "20261018t0000z"): print("---"); print(json.dumps(o))'
} > "$out"

docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 -schema-location default -schema-location "$crds" -summary - < "$out"
if grep -nE '"?type"?: "?(LoadBalancer|NodePort|ExternalName)|"?kind"?: "?(Ingress|PersistentVolumeClaim)("|$)|"?hostNetwork"?: true|"?hostPort"?:|"?hostPath"?:' "$out"; then
  echo "P4 renders a public service, an ingress, host access or a disk (PVC)"; exit 1
fi
if ! grep -q 'model_name: judge' "$out" || ! grep -q 'store_model_in_db: true' "$out"; then
  echo "P4's values (the judge alias, store_model_in_db) didn't reach the gateway's config"; exit 1
fi
echo "charts OK"
