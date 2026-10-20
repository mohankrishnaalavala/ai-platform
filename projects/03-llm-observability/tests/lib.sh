# shellcheck shell=bash
# Sourced by P3's session scripts (scale_from_zero.sh, gameday/gameday.sh). Checks kubectl points at this
# session's cluster, port-forwards the gateway, and reads its master key into memory (never printed; masked
# in Actions), as P2's tests/run.sh does. Sets GATEWAY_URL, MASTER_KEY, P2_TESTS and PROJECT. Call
# stop_port_forwards in the caller's EXIT trap.
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
P2_TESTS=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../02-llm-gateway/tests" && pwd)
PROJECT=$(gcloud config get project 2>/dev/null)
[ "$(kubectl config current-context)" = "gke_${PROJECT}_${ZONE:?set ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }

pfs=()
stop_port_forwards() { [ ${#pfs[@]} -eq 0 ] || kill "${pfs[@]}" 2>/dev/null || true; }
# port_forward NAMESPACE/SERVICE LOCAL:REMOTE: in the background, until it answers on LOCAL.
port_forward() {
  kubectl port-forward -n "${1%/*}" "svc/${1#*/}" "$2" >/dev/null & pfs+=($!)
  for _ in $(seq 30); do (exec 3<>"/dev/tcp/127.0.0.1/${2%:*}") 2>/dev/null && return; sleep 1; done
  echo "the port-forward to $1 didn't come up"; exit 1
}
port_forward gateway/gateway 14300:4000

impersonate=()
[ -n "${GITHUB_ACTIONS:-}" ] || impersonate=(--impersonate-service-account="deployer@$PROJECT.iam.gserviceaccount.com")
MASTER_KEY=$(gcloud secrets versions access latest --secret=litellm-master-key ${impersonate[@]+"${impersonate[@]}"} 2>/dev/null) || true
[ -n "$MASTER_KEY" ] || { echo "couldn't read the gateway's master key"; exit 1; }
[ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$MASTER_KEY"
export GATEWAY_URL=http://127.0.0.1:14300 MASTER_KEY P2_TESTS PROJECT
