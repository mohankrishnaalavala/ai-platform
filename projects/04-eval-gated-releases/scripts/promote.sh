#!/usr/bin/env bash
# Runs promote.py (the canary) against the session cluster: port-forwards to the gateway and to Prometheus,
# the gateway's master key read into memory only (as the deployer), then promote.py with these arguments.
# release-model.yml runs it; from the laptop it works the same way.
# Usage (from the repo root, layers 02, 03 and 04 deployed): ZONE=us-central1-c \
#   projects/04-eval-gated-releases/scripts/promote.sh [--candidate vertex_ai/gemini-3.5-flash] [--rollback]
set -euo pipefail
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
here=$(cd "$(dirname "$0")" && pwd)
project=$(gcloud config get project 2>/dev/null)
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE:?set ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }

# Unusual local ports, so a stray local server can't answer instead.
kubectl port-forward -n gateway svc/gateway 14300:4000 >/dev/null &
pf=$!
kubectl port-forward -n monitoring svc/prometheus-operated 19300:9090 >/dev/null &
pf2=$!
trap 'kill $pf $pf2 2>/dev/null' EXIT
up=
for _ in $(seq 30); do
  curl -sf http://127.0.0.1:14300/health/liveliness >/dev/null && curl -sf http://127.0.0.1:19300/-/ready >/dev/null && up=1 && break
  sleep 1
done
[ -n "$up" ] || { echo "the gateway or Prometheus isn't reachable (layers 02 and 03 deployed?)"; exit 1; }

impersonate=()
[ -n "${GITHUB_ACTIONS:-}" ] || impersonate=(--impersonate-service-account="deployer@$project.iam.gserviceaccount.com")
# ${a[@]+...}: an empty array is "unbound" under set -u in macOS bash 3.2.
master=$(gcloud secrets versions access latest --secret=litellm-master-key ${impersonate[@]+"${impersonate[@]}"} 2>/dev/null) || true
[ -n "$master" ] || { echo "couldn't read the gateway's master key"; exit 1; }
[ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$master"
GATEWAY_URL=http://127.0.0.1:14300 PROMETHEUS_URL=http://127.0.0.1:19300 MASTER_KEY="$master" python3 "$here/promote.py" "$@"
