#!/usr/bin/env bash
# kind (Kubernetes in Docker) test for the action gateway. No cloud: CI's action-gateway job runs it on
# every pull request. It makes a throwaway kind cluster on Kubernetes 1.36.4 (GKE's version), with its own
# kubeconfig so your kubectl context is untouched, and installs the scenarios' base state (evals/base.yaml)
# and the Helm chart as kind runs it (tests/kind/values.yaml: the CLI approver, a local audit file, a test
# Secret). Then it calls the gateway as an agent would, through kubectl port-forward:
#   1. the smoke test (tests/smoke_client.py):
#      - get_status, each write tool and undo in dev, all as the scoped ServiceAccount
#      - RBAC refusing a namespace outside dev, staging and prod
#      - two prod changes that wait for the CLI approver: this script rejects the first (its dry run changed
#        nothing) and approves the second, from inside the pod, as a person would
#   2. the 50 scenarios (evals/run_scenarios.py), with their metrics written to RESULTS_DIR (default: a new
#      temporary folder, printed at the end)
# The cluster is deleted at the end.
#
# Needs: Docker, kind, kubectl, helm, openssl, python3, and the image (IMAGE, default action-gateway:ci) built
# first. The agent runs in that image on Docker's host network (Linux). On macOS, Docker's host network can't
# reach the port-forward: set SMOKE_PYTHON to a local Python that has requirements.txt installed.
set -euo pipefail
cd "$(dirname "$0")/.."

image=${IMAGE:-action-gateway:ci}
node=kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed
cluster=p5-smoke
port=18080
url="http://127.0.0.1:$port/mcp"
results=${RESULTS_DIR:-$(mktemp -d)}
KUBECONFIG=$(mktemp)
export KUBECONFIG
gw=(kubectl -n action-gateway)
pf=""

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then  # the action lines hold ids and decisions, no object contents or tokens
    "${gw[@]}" logs deploy/action-gateway --all-containers --tail 60 || true
  fi
  [ -z "$pf" ] || { kill "$pf" && wait "$pf"; } 2>/dev/null || true
  kind delete cluster --name "$cluster" || true
  rm -f "$KUBECONFIG"
}
trap cleanup EXIT

kind create cluster --name "$cluster" --image "$node" --wait 120s
kind load docker-image "$image" --name "$cluster"
python3 -c 'import json; print(json.dumps({"apiVersion": "v1", "kind": "List", "items": json.load(open("evals/base.yaml"))["objects"]}))' \
  | kubectl apply -f -
kubectl apply -f helm/namespace.yaml
kubectl label namespace action-gateway owner=kind session=kind
# The test Secret in place of Secret Manager. There is no P2 gateway in kind, so the key is never used.
"${gw[@]}" create secret generic action-gateway-secrets --from-literal=action-gateway-team-key=kind-test-value

# This run's agent tokens: the smoke test's, and one per scenario team. Only their SHA-256 hashes go into
# the cluster; the tokens reach the agent through the environment (-e NAME), never a command line.
AGENT_TOKEN=$(openssl rand -hex 32)
AGENT_TOKENS=$(python3 -c 'import json, secrets; print(json.dumps({t: secrets.token_hex(32) for t in ("payments", "break-glass")}))')
export AGENT_TOKEN AGENT_TOKENS
hashes=$(python3 -c 'import hashlib, json, os
teams = {"smoke": os.environ["AGENT_TOKEN"]} | json.loads(os.environ["AGENT_TOKENS"])
print(json.dumps({hashlib.sha256(token.encode()).hexdigest(): team for team, token in teams.items()}))')
helm upgrade --install action-gateway helm/action-gateway -n action-gateway -f tests/kind/values.yaml \
  --set-json agentTokens="$hashes" \
  --set-file 'policies.gateway\.rego=policies/gateway.rego' --set-file 'policies.data\.json=policies/data.json' --wait --timeout 3m
# Tests only, never in the chart: the server reads its clock from this file, so a weekend can't change the
# answers. This script and the scenario runner write it with kubectl exec; the agent can't reach it.
"${gw[@]}" set env deploy/action-gateway -c gateway TEST_ONLY_CLOCK_FILE=/data/clock
"${gw[@]}" rollout status deploy/action-gateway --timeout=180s
"${gw[@]}" exec deploy/action-gateway -c gateway -- python -c "open('/data/clock', 'w').write('2026-11-04T10:00:00-06:00')"
for ns in dev staging prod; do kubectl -n "$ns" rollout status deploy/checkout --timeout=120s; done

"${gw[@]}" port-forward deploy/action-gateway "$port:8080" >/dev/null &
pf=$!
code=""
for _ in $(seq 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$url" || true)
  [ "$code" = 401 ] && break
  sleep 1
done
[ "$code" = 401 ] || { echo "the gateway didn't answer 401 to a call without a token (got '$code')"; exit 1; }
echo "a call without a token: HTTP 401"

# The agent: in the image on Linux (Docker's host network), or SMOKE_PYTHON. What the scenario results record:
COMMIT=$(git rev-parse HEAD 2>/dev/null || echo unknown)
IMAGE_ID=$(docker image inspect -f '{{.Id}}' "$image")
HOST_INFO="$(uname -sm), $(getconf _NPROCESSORS_ONLN) CPUs${ImageOS:+, GitHub-hosted runner $ImageOS ${ImageVersion:-}}"
export COMMIT NODE_IMAGE=$node IMAGE=$image IMAGE_ID HOST_INFO
mkdir -p "$results"
if [ -n "${SMOKE_PYTHON:-}" ]; then
  agent=("$SMOKE_PYTHON")
else
  agent=(docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp -e AGENT_TOKEN -e AGENT_TOKENS -e KUBECONFIG=/kubeconfig
         -e COMMIT -e NODE_IMAGE -e IMAGE -e IMAGE_ID -e HOST_INFO -v "$KUBECONFIG:/kubeconfig:ro" -v "$(command -v kubectl):/usr/local/bin/kubectl:ro"
         -v "$PWD:/p5:ro" -v "$results:/results" -w /p5 "$image" python)
  results_arg=/results
fi
"${agent[@]}" tests/smoke_client.py "$url" dev

# answer DECISION [ID TO SKIP]: wait for a pending approval and answer it with the CLI approver, inside the pod.
answer() {
  local id=""
  for _ in $(seq 60); do
    id=$("${gw[@]}" exec deploy/action-gateway -c gateway -- python approval.py list \
      | sed -n 's/.*"action_id": "\([^"]*\)".*/\1/p' | grep -vxF "${2:-none}" | head -1 || true)
    [ -n "$id" ] && break
    sleep 1
  done
  [ -n "$id" ] || { echo "no pending approval appeared"; exit 1; }
  "${gw[@]}" exec deploy/action-gateway -c gateway -- python approval.py "$1" "$id" --by ci-smoke >&2
  echo "$id"
}
"${agent[@]}" tests/smoke_client.py "$url" prod &
client=$!
first=$(answer reject)
answer approve "$first" >/dev/null
wait "$client"

echo "audit records (intents, and results by status); every executed action must have its intent:"
"${gw[@]}" exec deploy/action-gateway -c gateway -- python -c '
import collections, json, sys
recs = [json.loads(line) for line in open("/data/audit.jsonl")]
intents = {r["action_id"] for r in recs if r["record"] == "intent"}
executed = {r["action_id"] for r in recs if r["record"] == "result" and r["result"]["status"] == "executed"}
print({"intent": len(intents)}, dict(collections.Counter(r["result"]["status"] for r in recs if r["record"] == "result")))
sys.exit(0 if executed and executed <= intents else "an executed action has no intent record")'
echo "kind smoke test OK"

echo "the 50 scenarios:"
"${agent[@]}" evals/run_scenarios.py "$url" "${results_arg:-$results}"
echo "scenario results: $results/scenarios.md and scenarios.json"
