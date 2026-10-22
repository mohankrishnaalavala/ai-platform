#!/usr/bin/env bash
# kind (Kubernetes in Docker) smoke test for the action gateway. No cloud: CI's action-gateway job runs
# it on every pull request. It makes a throwaway kind cluster on Kubernetes 1.36.4 (GKE's version), with
# its own kubeconfig so your kubectl context is untouched, and installs the scenarios' base state
# (evals/base.yaml), rbac.yaml, and the gateway pod with its OPA sidecar running policies/. Then it calls
# the gateway as an agent would, through kubectl port-forward (tests/smoke_client.py):
#   - get_status, each write tool and undo in dev, all as the scoped ServiceAccount
#   - RBAC refusing a namespace outside dev, staging and prod
#   - two prod changes that wait for the CLI approver: this script rejects the first (its dry run changed
#     nothing) and approves the second, from inside the pod, as a person would
# The cluster is deleted at the end.
#
# Needs: Docker, kind, kubectl, openssl, python3, and the image (IMAGE, default action-gateway:ci) built first.
# The agent runs in that image on Docker's host network (Linux). On macOS, Docker's host network can't
# reach the port-forward: set SMOKE_PYTHON to a local Python that has requirements.txt installed.
set -euo pipefail
cd "$(dirname "$0")/.."

image=${IMAGE:-action-gateway:ci}
node=kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed
cluster=p5-smoke
port=18080
url="http://127.0.0.1:$port/mcp"
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
kubectl create namespace action-gateway
kubectl label namespace action-gateway app=ai-platform layer=05
kubectl apply -f rbac.yaml

token=$(openssl rand -hex 32)  # this run's agent token; only its SHA-256 goes into the cluster
hash=$(printf '%s' "$token" | openssl dgst -sha256 | sed 's/^.*= //')
"${gw[@]}" create configmap agent-tokens --from-literal=tokens.json="{\"$hash\": \"smoke\"}"
"${gw[@]}" create configmap policies --from-file=policies
"${gw[@]}" create configmap test-clock --from-literal=now=2026-11-04T10:00:00-06:00
kubectl apply -f tests/kind/gateway.yaml
"${gw[@]}" rollout status deploy/action-gateway --timeout=180s
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

if [ -n "${SMOKE_PYTHON:-}" ]; then
  agent=(env AGENT_TOKEN="$token" "$SMOKE_PYTHON" tests/smoke_client.py "$url")
else
  agent=(docker run --rm --network host -e AGENT_TOKEN="$token" -v "$PWD/tests:/tests:ro" "$image" python /tests/smoke_client.py "$url")
fi
"${agent[@]}" dev

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
"${agent[@]}" prod &
client=$!
first=$(answer reject)
answer approve "$first" >/dev/null
wait "$client"

echo "audit records by result:"
"${gw[@]}" exec deploy/action-gateway -c gateway -- python -c \
  'import collections, json; print(dict(collections.Counter(json.loads(l)["result"]["status"] for l in open("/data/audit.jsonl"))))'
echo "kind smoke test OK"
