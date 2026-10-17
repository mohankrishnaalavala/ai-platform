#!/usr/bin/env bash
# Cold starts, timed phase by phase from Kubernetes timestamps (timings.py): scale vLLM to 0, wait
# until no GPU node is left, ask for 1 replica, and time node ready -> image pulled -> model loaded
# -> first token. Usage (from the repo root, in a session with `make up LAYERS=01`):
#   ZONE=... [CONFIG=...] coldstart.sh optimized 3   # three cold starts of the optimized path
#   ZONE=... [CONFIG=...] coldstart.sh baseline 3    # switch to Docker Hub + Hugging Face, then three
#   ZONE=... [CONFIG=...] coldstart.sh removal       # only time how long the idle GPU node takes to go
# KEDA is paused (autoscaling.keda.sh/paused-replicas) while it runs, so it doesn't undo the scaling.
# After `baseline` the deployment stays on the baseline path; run it last, or redeploy with deploy.sh.
set -euo pipefail
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}

here=$(cd "$(dirname "$0")" && pwd)
variant=${1:-}; runs=${2:-1}
case $variant in
  optimized|removal) pool=gpu ;;
  baseline) pool=gpu-baseline ;;
  *) echo "usage: coldstart.sh optimized|baseline|removal [runs]"; exit 1 ;;
esac
[ "$variant" != removal ] || runs=1
[[ "$runs" =~ ^[0-9]+$ ]] || { echo "runs must be a number"; exit 1; }
config=${CONFIG:-config/models/qwen2.5-7b-bf16.yaml}
val() { awk -v k="$1:" '$1 == k {print $2; exit}' "$config"; }
name=$(val name); hf=$(val hfId)
project=$(gcloud config get project 2>/dev/null)
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
profile=$(gcloud container clusters describe ai-platform --location="$ZONE" --format='value(autoscaling.autoscalingProfile)')
: "${name:?no name in $config}" "${hf:?no hfId in $config}" "${session:?no session label}"
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }
served=$(kubectl get deployment vllm -n vllm -o jsonpath='{.metadata.labels.model-config}')
[ "$served" = "$name" ] || { echo "vLLM serves '$served', not '$name': deploy that config first"; exit 1; }

hash=$(python3 "$here/../../../scripts/config_hash.py" "$config")
results="gs://$project-results/p1/$hash/$session/coldstart/$(date -u +%Y%m%dt%H%M%Sz)"
out=$(mktemp -d)
# A fresh token per upload: the deployer's access token lasts an hour, a run of cold starts longer.
upload() { GCS_TOKEN=$(gcloud auth print-access-token) python3 "$here/upload.py" "$results" "$@"; }
# Raw output goes up too, with the project ID taken out.
scrub() { sed "s/$project/<project>/g" "$1" > "$2"; }
pause() { kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas="$1" --overwrite >/dev/null; }
replicas() { kubectl get deployment vllm -n vllm -o jsonpath='{.spec.replicas}'; }
on_pool() { kubectl get deployment vllm -n vllm -o jsonpath='{.spec.template.spec.nodeSelector.cloud\.google\.com/gke-nodepool}'; }
# Hand scaling back to KEDA at the end; after `removal` the session ends anyway, so no GPU is restarted.
[ "$variant" = removal ] \
  || trap 'kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas- >/dev/null || echo "WARNING: KEDA is still paused"' EXIT

for n in $(seq "$runs"); do
  # 1. Down to zero: no vLLM pod, then no GPU node. With a GPU quota of 1, no new GPU node can
  #    start before the old one is gone, and only a new node makes it a real cold start.
  pause 0
  for _ in $(seq 60); do [ -z "$(kubectl get pods -n vllm -l app.kubernetes.io/name=vllm -o name)" ] && break; sleep 5; done
  [ -z "$(kubectl get pods -n vllm -l app.kubernetes.io/name=vllm -o name)" ] || { echo "the vLLM pod didn't stop within 5 minutes"; exit 1; }
  start=$(date +%s)
  while [ -n "$(kubectl get nodes -l cloud.google.com/gke-accelerator -o name)" ]; do
    [ $(( $(date +%s) - start )) -lt 1800 ] || { echo "a GPU node is still there after 30 minutes"; exit 1; }
    sleep 10
  done
  removal=$(( $(date +%s) - start ))
  echo "run $n: no GPU node left ${removal}s after vLLM stopped (autoscaler profile $profile)"
  if [ "$variant" = removal ]; then
    printf '{"variant": "removal", "profile": "%s", "removal_wait_s": %s, "session": "%s"}\n' \
      "$profile" "$removal" "$session" > "$out/removal-1.json"
    upload "$out/removal-1.json"
    exit 0
  fi
  if [ "$variant" = baseline ] && [ "$(on_pool)" != gpu-baseline ]; then
    CONFIG="$config" VARIANT=baseline bash "$here/../deploy.sh"
  fi
  [ "$(on_pool)" = "$pool" ] || { echo "vLLM is set up for pool $(on_pool), not $pool ($variant)"; exit 1; }

  # 2. Ask for one replica. KEDA should scale to the paused count; if it doesn't within a minute,
  #    scale directly (paused, KEDA won't undo it). Then wait until the model is loaded.
  asked=$(date +%s)
  pause 1
  scaled_by=keda
  for _ in $(seq 12); do [ "$(replicas)" = 1 ] && break; sleep 5; done
  [ "$(replicas)" = 1 ] || { kubectl scale deployment vllm -n vllm --replicas=1; scaled_by=manual; }
  kubectl rollout status deployment/vllm -n vllm --timeout=40m
  pod=$(kubectl get pods -n vllm -l app.kubernetes.io/name=vllm --field-selector=status.phase=Running -o name | head -1)

  # 3. First token: a one-token completion from inside the pod (no port-forward in the way).
  first=$(kubectl exec -n vllm "$pod" -c vllm -- python3 -c '
import json, sys, time, urllib.request
body = json.dumps({"model": sys.argv[1], "prompt": "Hello", "max_tokens": 1}).encode()
req = urllib.request.Request("http://localhost:8000/v1/completions", data=body, headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=300).read()
print(time.time())' "$hf")

  # More than one vLLM pod means one was replaced on the way up (for example, spot taken away).
  seen=$(kubectl get pods -n vllm -l app.kubernetes.io/name=vllm -o name | wc -l | tr -d ' ')
  kubectl get -n vllm "$pod" -o json > "$out/pod.json"
  kubectl get events -n vllm --field-selector "involvedObject.name=${pod#pod/}" -o json > "$out/events.json"
  kubectl get node "$(jq -r .spec.nodeName "$out/pod.json")" -o json > "$out/node.json"
  python3 "$here/timings.py" "$out/pod.json" "$out/events.json" "$out/node.json" "$asked" "$first" \
    variant="$variant" run="$n" config="$name" config_hash="$hash" session="$session" profile="$profile" \
    removal_wait_s="$removal" scaled_by="$scaled_by" vllm_pods_seen="$seen" \
    machine="$(jq -r '.metadata.labels["node.kubernetes.io/instance-type"]' "$out/node.json")" \
    spot="$(jq -r '.metadata.labels["cloud.google.com/gke-spot"] // "false"' "$out/node.json")" \
    gke="$(jq -r .status.nodeInfo.kubeletVersion "$out/node.json")" > "$out/$variant-$n.json"
  cat "$out/$variant-$n.json"
  for f in pod events node; do scrub "$out/$f.json" "$out/$variant-$n-$f.json"; done
  # Saved after every run, so a session that ends early keeps the runs it finished.
  upload "$out/$variant-$n.json" "$out/$variant-$n-pod.json" "$out/$variant-$n-events.json" "$out/$variant-$n-node.json"
done
