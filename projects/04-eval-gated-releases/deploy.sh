#!/usr/bin/env bash
# Deploy P4 into the running session cluster: P2's gateway release upgraded with P4's values (the judge
# alias, and model storage in the gateway's database so promote.py can change canary weights at runtime).
# Needs P2 first, and P3 for promote.py's error-rate check. P4 adds no pod of its own here: promote.py's
# probe Job exists only while a canary runs.
# Usage (from the repo root): ZONE=us-central1-c projects/04-eval-gated-releases/deploy.sh
#
# Keeping P2's values: P2's deploy.sh gives the release project, labels.owner, labels.session and, for the
# session's PII test, mockProvider.enabled, with --set. A plain `helm upgrade -f` here would drop them (Helm
# starts again from the chart's defaults). --reuse-values would keep them but also freeze the chart's
# defaults from P2's last install. --reset-then-reuse-values takes the chart's current defaults, then every
# value the last release was given, then P4's file on top. Running it again changes nothing: the same
# config renders, so the gateway pod isn't restarted.
# The other way round: P2's deploy.sh, run again, doesn't pass P4's file, so P4's values go (as P3's
# collector values go). Run both, in order: make deploy LAYERS="02 04". The canary's deployments live in
# the gateway's Postgres, which no helm upgrade touches.
set -euo pipefail
# Same settings as the Makefile: never the machine's default project or kubeconfig.
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
here=$(cd "$(dirname "$0")" && pwd)
project=$(gcloud config get project 2>/dev/null)
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE:?set ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }
helm status gateway -n gateway >/dev/null 2>&1 || { echo "deploy P2 first (make up LAYERS=\"02 03 04\")"; exit 1; }

helm upgrade gateway "$here/../02-llm-gateway/helm/gateway" -n gateway --reset-then-reuse-values \
  -f "$here/helm/gateway-canary.yaml" --wait --timeout 15m || {
  kubectl get pods,events -n gateway --sort-by=.metadata.creationTimestamp | tail -30
  exit 1
}
