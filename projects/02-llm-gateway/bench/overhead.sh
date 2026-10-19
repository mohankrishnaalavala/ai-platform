#!/usr/bin/env bash
# P2-08: what the gateway adds to latency. P1's benchmark Job runs the same chat load (512 tokens in,
# 128 out, OpenAI chat API) twice: straight to vLLM, then through the gateway (alias chat-default,
# so masking and budgets run as they do for a team). Results go to the results bucket under
# p2/overhead/<session>/{direct,gateway}/<time>/. The deployer may only add to that bucket, not read it,
# so overhead.py compares the two runs once they are copied into results/ (as P1 did with cost.py).
# What "added" includes: LiteLLM, PII masking of every prompt (random tokens here), and CPU contention,
# since the client shares the system node with the gateway (kubectl top and node allocations follow).
# Any request that fell back to the hosted model makes the gateway run invalid: the script then fails.
# If the step's time limit kills the script, the key and Secret stay until the cluster is destroyed.
# The gateway run uses a key made for it, handed to the Job as a Kubernetes Secret (OPENAI_API_KEY,
# read from the environment, never an argument), then revoked and the Secret deleted. It also counts
# requests that fell back to the hosted model during the run (spend logs), which would skew it.
# Usage (from the repo root, with `make up LAYERS="01 02"`): ZONE=... CONFIG=<the served config> overhead.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
project=$(gcloud config get project 2>/dev/null)
owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
[ -n "$owner" ] && [ -n "$session" ] || { echo "couldn't read the owner or session label"; exit 1; }
: "${CONFIG:?set CONFIG to the model config vLLM serves}"
concurrency=${CONCURRENCY:-10 50}

kubectl port-forward -n gateway svc/gateway 14200:4000 >/dev/null &
pf=$!
key=
cleanup() {
  [ -z "$key" ] || admin /key/delete '{"keys": ["'"$key"'"]}' >/dev/null || echo "couldn't revoke the benchmark key"
  kubectl delete secret overhead-bench-key -n vllm --ignore-not-found >/dev/null
  kill $pf 2>/dev/null || true
}
trap cleanup EXIT
up=; for _ in $(seq 30); do curl -sf http://127.0.0.1:14200/health/liveliness >/dev/null && up=1 && break; sleep 1; done
[ -n "$up" ] || { echo "the gateway isn't reachable"; exit 1; }

impersonate=()
[ -n "${GITHUB_ACTIONS:-}" ] || impersonate=(--impersonate-service-account="deployer@$project.iam.gserviceaccount.com")
master=$(gcloud secrets versions access latest --secret=litellm-master-key ${impersonate[@]+"${impersonate[@]}"} 2>/dev/null) || true
[ -n "$master" ] || { echo "couldn't read the gateway's master key"; exit 1; }
[ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$master"
# The gateway's admin API, with the master key in the environment (not in a process argument).
admin() { MASTER="$master" python3 -c '
import json, os, sys, urllib.request
req = urllib.request.Request("http://127.0.0.1:14200" + sys.argv[1], data=sys.argv[2].encode(), method="POST",
                             headers={"Authorization": "Bearer " + os.environ["MASTER"], "Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=30).read().decode())' "$1" "$2"; }

key=$(admin /key/generate '{"key_alias": "overhead-bench", "max_budget": 5}' | python3 -c 'import json, sys; print(json.load(sys.stdin)["key"])')
[ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$key"
printf '%s' "$key" | kubectl create secret generic overhead-bench-key -n vllm --from-file=OPENAI_API_KEY=/dev/stdin \
  --dry-run=client -o yaml | kubectl label --local -f - app=ai-platform layer=02 owner="$owner" session="$session" -o yaml \
  | kubectl apply -f - >/dev/null

# Requests answered by anything but our Qwen model, i.e. fallbacks (the spend logs record each call's model).
fallbacks() { kubectl exec -n gateway deployment/postgres -- psql -U litellm -d litellm -Atc \
  "select count(*) from \"LiteLLM_SpendLogs\" where model not ilike '%qwen%'"; }
# The count, once LiteLLM's batched spend-log writes have stopped changing it.
settled() { local a b; a=$(fallbacks); for _ in $(seq 12); do sleep 5; b=$(fallbacks); [ "$a" = "$b" ] && break; a=$b; done; echo "$a"; }
common=(SCENARIOS=chat CONCURRENCY="$concurrency" BACKEND=openai-chat ENDPOINT=/v1/chat/completions ZONE="$ZONE" CONFIG="$CONFIG")

# Preflight, before any GPU time is spent: the PII analyzer is ready (the gateway refuses every request
# while it isn't), the spend-log query works, and the key gets an answer from vLLM.
kubectl rollout status deployment/presidio-analyzer -n gateway --timeout=20m  # covers one restart, as in tests/run.sh
fallbacks >/dev/null
KEY="$key" python3 -c '
import json, os, urllib.request
req = urllib.request.Request("http://127.0.0.1:14200/v1/chat/completions", method="POST",
                             data=json.dumps({"model": "chat-default", "max_tokens": 5, "messages": [{"role": "user", "content": "ok"}]}).encode(),
                             headers={"Authorization": "Bearer " + os.environ["KEY"], "Content-Type": "application/json"})
r = urllib.request.urlopen(req, timeout=60)
fallbacks = r.headers.get("x-litellm-attempted-fallbacks", "absent")
print(f"preflight: status {r.status}, attempted fallbacks {fallbacks}")
raise SystemExit(0 if r.status == 200 and fallbacks in ("absent", "0") else 1)'

echo "== straight to vLLM"
env "${common[@]}" RESULTS_PREFIX="p2/overhead/$session/direct" bash "$repo/projects/01-gpu-serving/bench/bench.sh"
kubectl top pods -n gateway 2>/dev/null || true

echo "== through the gateway (chat-default)"
before=$(settled)
t0=$(date -u +%Y-%m-%dT%H:%M:%S)
env "${common[@]}" RESULTS_PREFIX="p2/overhead/$session/gateway" TARGET_URL=http://gateway.gateway.svc:4000 \
  TARGET_MODEL=chat-default KEY_SECRET=overhead-bench-key bash "$repo/projects/01-gpu-serving/bench/bench.sh"
fell_back=$(( $(settled) - before ))
echo "requests that fell back to the hosted model during the gateway run: $fell_back"
# What those rows are: session 3 counted 3 and couldn't say which model, provider or status they had.
kubectl exec -n gateway deployment/postgres -- psql -U litellm -d litellm -c "select model, custom_llm_provider,
  status, call_type, count(*) from \"LiteLLM_SpendLogs\" where \"startTime\" >= '$t0' and model not ilike '%qwen%'
  group by 1, 2, 3, 4" || true
kubectl top pods -n gateway 2>/dev/null || true
kubectl describe nodes -l cloud.google.com/gke-nodepool=system | grep -A 8 'Allocated resources' || true
[ "$fell_back" = 0 ] || { echo "INVALID: $fell_back requests fell back to the hosted model during the gateway run"; exit 1; }
