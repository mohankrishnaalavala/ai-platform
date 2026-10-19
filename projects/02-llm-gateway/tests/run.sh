#!/usr/bin/env bash
# P2 checks against the running session (`make up LAYERS=02`, deployed with MOCK_PROVIDER=1): the
# same test files as CI, through port-forwards, with the master key read into memory only. Then one
# timed `make team`, the PII scan of logs and spend logs, and fail-closed with the PII analyzer scaled
# to 0 (put back after). With no vLLM in the session, chat-default falls back to the real hosted model.
# `run.sh vllm-down` (a GPU session, last): vLLM answers a call, then KEDA takes it to 0 replicas and
# chat-default must fall back to the hosted model; leaves vLLM at 0 (teardown follows).
# `run.sh demo` (vLLM up) and `run.sh demo vllm-down` (after vllm-down): demo.py's transcript for the blog.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
cd "$here"  # `python3 -m unittest <module>` imports from the current directory; make runs from the root
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
fail=0
run() { local name=$1; shift; if "$@"; then echo "PASS: $name"; else echo "FAIL: $name"; fail=1; fi; }
dump=$(mktemp)

# Unusual local ports, so a stray local server can't answer instead (make team uses 14000).
kubectl port-forward -n gateway svc/gateway 14100:4000 >/dev/null &
pf=$!
kubectl port-forward -n gateway svc/mock-provider 18100:8000 >/dev/null &
pf2=$!
# The analyzer goes back to 1 replica even if a check fails.
trap 'kubectl scale deployment presidio-analyzer -n gateway --replicas=1 >/dev/null 2>&1; kill $pf $pf2 2>/dev/null; rm -f "$dump"' EXIT
for _ in $(seq 30); do
  curl -sf http://127.0.0.1:14100/health/liveliness >/dev/null && curl -sf http://127.0.0.1:18100/health >/dev/null && break
  sleep 1
done
if ! kill -0 $pf 2>/dev/null || ! kill -0 $pf2 2>/dev/null; then
  echo "FAIL: a port-forward died (was the gateway deployed with MOCK_PROVIDER=1?)"; exit 1
fi

project=$(gcloud config get project 2>/dev/null)
impersonate=()
[ -n "${GITHUB_ACTIONS:-}" ] || impersonate=(--impersonate-service-account="deployer@$project.iam.gserviceaccount.com")
# ${a[@]+...}: an empty array is "unbound" under set -u in macOS bash 3.2.
master=$(gcloud secrets versions access latest --secret=litellm-master-key ${impersonate[@]+"${impersonate[@]}"} 2>/dev/null) || true
[ -n "$master" ] || { echo "FAIL: couldn't read the gateway's master key"; exit 1; }
[ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$master"
export GATEWAY_URL=http://127.0.0.1:14100 MASTER_KEY="$master" MOCK_HOSTED_URL=http://127.0.0.1:18100 HOSTED_ALIAS=chat-hosted-mock
# The gateway refuses every request while the PII analyzer is down (fail closed), and the analyzer
# needs some seconds after a restart before it is ready: session 2's next step failed on exactly that.
# 20 min: one start can hang until its startup probe restarts it after 10 min (session 3); the next one comes up.
analyzer_ready() { kubectl rollout status deployment/presidio-analyzer -n gateway --timeout=20m; }
analyzer_ready

if [ "${1:-}" = demo ]; then
  if [ "${2:-}" = vllm-down ] && [ -n "$(kubectl get pods -n vllm -l app.kubernetes.io/name=vllm -o name)" ]; then
    echo "FAIL: vLLM is still running (run.sh vllm-down first)"; exit 1
  fi
  python3 demo.py ${2:+"$2"}
  exit
fi

if [ "${1:-}" = vllm-down ]; then
  run "chat-default answered by vLLM itself" python3 -c '
from client import chat, delete_key, new_key
key = new_key("vllm-up")
status, _, headers, seconds = chat(key, "chat-default", "Reply with the single word: ok")
delete_key(key)
fallbacks = headers.get("x-litellm-attempted-fallbacks", "absent")
print(f"vLLM up: status {status}, {seconds * 1000:.0f} ms, attempted fallbacks {fallbacks}")
raise SystemExit(0 if status == 200 and fallbacks in ("absent", "0") else 1)'
  # KEDA would bring vLLM back; pausing it at 0 replicas takes vLLM away, as a scale-down or crash would.
  kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas=0 --overwrite
  kubectl wait --for=delete pod -n vllm -l app.kubernetes.io/name=vllm --timeout=300s
  run "fallback to the hosted model after vLLM went away" env VLLM_DOWN=1 python3 -m unittest -v test_fallback
  exit $fail
fi

# Smoke, budget, rate limit, keys, and PII on the hosted route (to the in-cluster mock, which records it).
run "gateway tests (smoke, budget, rate limit, keys, PII direct)" python3 -m unittest discover -s "$here" -v

# No vLLM in this session: chat-default must fall back to the real hosted model, keyless.
if ! kubectl get deployment vllm -n vllm >/dev/null 2>&1; then
  run "fallback to the hosted model (no vLLM)" env VLLM_DOWN=1 python3 -m unittest -v test_fallback
fi

# One onboarding, timed end to end (port-forward, master key, team, key). The key line isn't shown;
# onboard_team.py's ::add-mask:: line is kept, so the runner masks the key too.
now() { python3 -c "import time; print(time.time())"; }  # macOS date has no %N
start=$(now)
if make -C "$repo" --no-print-directory team NAME="shakedown-$(date +%H%M%S)" BUDGET_USD=1 RPM=10 > "$dump" 2>&1; then
  grep -v '^key ' "$dump"
  echo "PASS: make team (end to end $(python3 -c "import sys; print(f'{float(sys.argv[2]) - float(sys.argv[1]):.1f}')" "$start" "$(now)") s)"
else
  grep -v '^key ' "$dump"; echo "FAIL: make team"; fail=1
fi

# No raw PII in the gateway's logs, its collector's output or its spend logs (container logs reach Cloud Logging).
sleep 15  # LiteLLM writes spend logs in batches
: > "$dump"
for d in litellm otel-collector presidio-analyzer presidio-anonymizer; do
  kubectl logs -n gateway "deployment/$d" --all-containers --tail=-1 >> "$dump" 2>&1 || true
done
psql() { kubectl exec -n gateway deployment/postgres -- psql -U litellm -d litellm -Atc "$1"; }
rows=$(psql 'select count(*) from "LiteLLM_SpendLogs"' 2>/dev/null) || rows=0
if [ "${rows:-0}" -gt 0 ] && psql 'select row_to_json(t) from "LiteLLM_SpendLogs" t' >> "$dump"; then
  echo "dump: $(wc -l < "$dump") lines, $rows spend-log rows"
  run "no raw PII in logs, traces or spend logs" env PII_DUMP_FILE="$dump" python3 -m unittest -v test_pii.NoRawPiiInLogs
else
  echo "FAIL: no spend-log rows to check (or psql failed): $rows"; fail=1
fi

# Fail closed: with the analyzer gone, a request is refused and never reaches the provider.
kubectl scale deployment presidio-analyzer -n gateway --replicas=0
kubectl wait --for=delete pod -n gateway -l app.kubernetes.io/name=presidio-analyzer --timeout=120s
run "fail closed (PII analyzer scaled to 0)" env PRESIDIO_DOWN=1 python3 -m unittest -v test_pii.FailClosed
kubectl scale deployment presidio-analyzer -n gateway --replicas=1
analyzer_ready

kubectl top pods -n gateway 2>/dev/null || true
[ "$fail" = 0 ] && echo "all P2 checks passed"
exit $fail
