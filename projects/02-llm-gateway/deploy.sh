#!/usr/bin/env bash
# Deploy P2 into the running session cluster: the namespace, the OTel Collector, then the gateway
# (LiteLLM + Postgres). Works with or without P1: with `make up LAYERS=02` alone, chat-default has no
# backend, and only chat-hosted answers.
# Usage (from the repo root): ZONE=us-central1-c [MOCK_PROVIDER=1] projects/02-llm-gateway/deploy.sh
# MOCK_PROVIDER=1 adds the test-only mock provider (alias chat-hosted-mock) for the session's PII test.
set -euo pipefail
# Same settings as the Makefile: never the machine's default project or kubeconfig.
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}

here=$(cd "$(dirname "$0")" && pwd)
project=$(gcloud config get project 2>/dev/null)
owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
# The cluster's own session label, so Kubernetes objects match the Terraform resources.
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
[ -n "$owner" ] && [ -n "$session" ] || { echo "couldn't read the owner or session label"; exit 1; }
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }

kubectl apply -f "$here/helm/namespaces.yaml"
kubectl label namespace gateway owner="$owner" session="$session" --overwrite

# New random secrets for each session, on the first install only: a second set would lock LiteLLM
# out of the Postgres that was initialised with the first. Values are piped, never printed or logged,
# and the older versions are destroyed (destroyed versions stop billing).
# Any other helm error stops the script here (set -e) instead of counting as a first install.
releases=$(helm list -n gateway -q)
if ! grep -qx gateway <<< "$releases"; then
  for secret in litellm-master-key gateway-postgres-password; do
    case $secret in
      litellm-master-key) value="sk-$(openssl rand -hex 32)" ;;  # LiteLLM wants the sk- prefix
      *) value=$(openssl rand -hex 24) ;;
    esac
    printf '%s' "$value" | gcloud secrets versions add "$secret" --data-file=- >/dev/null
    unset value
    latest=$(gcloud secrets versions list "$secret" --filter='state=ENABLED' --sort-by=~createTime --limit=1 --format='value(name.basename())')
    for old in $(gcloud secrets versions list "$secret" --filter='state!=DESTROYED' --format='value(name.basename())'); do
      [ "$old" = "$latest" ] || gcloud secrets versions destroy "$old" --secret="$secret" --quiet >/dev/null
    done
    echo "$secret: new version $latest; older versions destroyed"
  done
fi

labels=(--set-string "additionalLabels.owner=$owner" --set-string "additionalLabels.session=$session"
        --set-string "podLabels.owner=$owner" --set-string "podLabels.session=$session")
helm upgrade --install otel-collector opentelemetry-collector --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
  --version 0.174.2 -n gateway -f "$here/helm/otel-collector.yaml" "${labels[@]}" --wait --timeout 5m
mock=()
if [ "${MOCK_PROVIDER:-}" = 1 ]; then
  # The same file CI runs; a ConfigMap, so no image of our own is built or pushed.
  kubectl create configmap mock-provider -n gateway --from-file=server.py="$here/tests/mock_provider/server.py" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl label configmap mock-provider -n gateway app=ai-platform layer=02 owner="$owner" session="$session" --overwrite
  mock=(--set mockProvider.enabled=true)
fi
helm upgrade --install gateway "$here/helm/gateway" -n gateway --set-string project="$project" \
  --set-string labels.owner="$owner" --set-string labels.session="$session" ${mock[@]+"${mock[@]}"} --wait --timeout 15m || {
  kubectl get pods,events -n gateway --sort-by=.metadata.creationTimestamp | tail -30
  exit 1
}
