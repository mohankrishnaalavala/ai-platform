#!/usr/bin/env bash
# Scale-out under queue pressure: flood vLLM with long requests until its queue passes KEDA's
# threshold, and record every 5 s what KEDA sees (queue length), what the HPA wants and has, and the
# vLLM pods, until a second replica is ready or 12 minutes pass. With a GPU quota of 1 the second
# replica can't get a GPU: the record then shows KEDA asking for it and why it stayed Pending.
# Usage (from the repo root, in a session with `make up LAYERS=01`): ZONE=... [CONFIG=...] scaleout.sh
set -euo pipefail
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}

here=$(cd "$(dirname "$0")" && pwd)
config=${CONFIG:-config/models/qwen2.5-7b-bf16.yaml}
val() { awk -v k="$1:" '$1 == k {print $2; exit}' "$config"; }
name=$(val name); hf=$(val hfId); rev=$(val revision)
project=$(gcloud config get project 2>/dev/null)
owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
: "${name:?no name in $config}" "${hf:?no hfId in $config}" "${rev:?no revision in $config}" "${owner:?no owner label}" "${session:?no session label}"
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }
served=$(kubectl get deployment vllm -n vllm -o jsonpath='{.metadata.labels.model-config}')
[ "$served" = "$name" ] || { echo "vLLM serves '$served', not '$name': deploy that config first"; exit 1; }

hash=$(python3 "$here/../../../scripts/config_hash.py" "$config")
results="gs://$project-results/p1/$hash/$session/scaleout/$(date -u +%Y%m%dt%H%M%Sz)"
out=$(mktemp -d)

# KEDA in charge, one replica ready.
kubectl annotate scaledobject vllm -n vllm autoscaling.keda.sh/paused-replicas- >/dev/null \
  || echo "WARNING: couldn't remove KEDA's pause; check the ScaledObject"
kubectl rollout status deployment/vllm -n vllm --timeout=40m
image=$(kubectl get deployment vllm -n vllm -o jsonpath='{.spec.template.spec.containers[0].image}')

# The load: 128 long (RAG-like) requests at a time, far more than one replica batches at once.
trap 'kubectl delete job load -n vllm --ignore-not-found --wait=false >/dev/null' EXIT
kubectl delete job load -n vllm --ignore-not-found --wait
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: load
  namespace: vllm
  labels: {app: ai-platform, layer: "01", owner: "$owner", session: "$session"}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 1200
  template:
    metadata:
      labels: {app: ai-platform, layer: "01", owner: "$owner", session: "$session"}
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      enableServiceLinks: false  # else VLLM_PORT=tcp://... breaks vLLM's CLI
      nodeSelector:
        cloud.google.com/gke-nodepool: system
      containers:
        - name: load
          image: "$image"
          command: [bash, -c]
          args:
            - >-
              python3 -c "from huggingface_hub import snapshot_download as d;
              d('$hf', revision='$rev', local_dir='/tmp/tok', allow_patterns=['tokenizer*', 'vocab.json', 'merges.txt', 'config.json'])"
              && vllm bench serve --base-url http://vllm.vllm.svc:8000 --model '$hf' --tokenizer /tmp/tok
              --dataset-name random --random-input-len 4000 --random-output-len 256 --random-range-ratio 0 --ignore-eos
              --num-prompts 1280 --max-concurrency 128 --request-rate inf --seed 1 --disable-tqdm
          env:
            - {name: VLLM_NO_USAGE_STATS, value: "1"}
            - {name: HF_HUB_DISABLE_TELEMETRY, value: "1"}
          resources:
            requests: {cpu: "1", memory: 2Gi}
EOF

jq -nc --arg t "$(date -u +%FT%TZ)" '{event: "load_started", t: $t}' | tee "$out/scaleout-series.jsonl"

# Sample every 5 s: the queue length KEDA reads (metric s0-prometheus: KEDA's name for the
# ScaledObject's first trigger), the HPA's desired/current replicas, and the vLLM pods.
metric="/apis/external.metrics.k8s.io/v1beta1/namespaces/vllm/s0-prometheus?labelSelector=scaledobject.keda.sh%2Fname%3Dvllm"
start=$(date +%s)
while [ $(( $(date +%s) - start )) -lt 720 ]; do
  queue=$(kubectl get --raw "$metric" 2>/dev/null | jq -r '.items[0].value // "none"' || echo none)
  hpa=$(kubectl get hpa keda-hpa-vllm -n vllm -o json 2>/dev/null \
    | jq -c '{desired: .status.desiredReplicas, current: .status.currentReplicas}' || echo null)
  pods=$(kubectl get pods -n vllm -l app.kubernetes.io/name=vllm -o json | jq -c '[.items[] | {name: .metadata.name,
    created: .metadata.creationTimestamp, phase: .status.phase,
    ready: ([.status.conditions[]? | select(.type == "Ready") | .status] | first // "False"),
    readyAt: ([.status.conditions[]? | select(.type == "Ready" and .status == "True") | .lastTransitionTime] | first)}]' \
    || echo '[]')
  jq -nc --arg t "$(date -u +%FT%TZ)" --arg q "$queue" --argjson h "$hpa" --argjson p "$pods" \
    '{t: $t, queue: $q, hpa: $h, pods: $p}' | tee -a "$out/scaleout-series.jsonl"
  [ "$(jq '[.[] | select(.ready == "True")] | length' <<< "$pods")" -lt 2 ] || break
  sleep 5
done

# What happened to replica 2: the HPA, the scheduler and the cluster autoscaler explain it. A quota
# failure shows in the GPU node group's errors (readable by the deployer, unlike the autoscaler log).
kubectl get events -n vllm -o json | jq '[.items[] | select(.involvedObject.name | test("^vllm|^keda-hpa-vllm"))
  | {reason, object: .involvedObject.name, message, first: .firstTimestamp, last: (.lastTimestamp // .eventTime)}]' \
  > "$out/scaleout-events.json"
# Failures here must not stop the upload below: the series and the verdict matter more.
# The GPU pool has one node group per zone (.../zones/<zone>/instanceGroupManagers/<name>).
urls=$(gcloud container node-pools describe gpu --cluster=ai-platform --location="$ZONE" --format='value(instanceGroupUrls)' | tr ';' ' ' || true)
for url in $urls; do
  z=${url#*/zones/}; z=${z%%/*}
  echo "== $z"
  gcloud compute instance-groups managed list-errors "${url##*/}" --zone="$z" --format=json || echo '"could not list"'
done > "$out/gpu-group-errors.txt"
second=$(kubectl get pods -n vllm -l app.kubernetes.io/name=vllm --sort-by=.metadata.creationTimestamp -o name | sed -n 2p)
if [ -n "$second" ] && [ "$(kubectl get -n vllm "$second" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" = True ]; then
  # Proof that replica 2 served traffic: its own request counter, read until it moves (up to 60 s).
  served=0
  for _ in $(seq 12); do
    served=$(kubectl exec -n vllm "$second" -c vllm -- python3 -c '
import urllib.request
m = urllib.request.urlopen("http://localhost:8000/metrics").read().decode()
print(int(sum(float(l.split()[-1]) for l in m.splitlines() if l.startswith("vllm:request_success_total"))))' || echo 0)
    [ "$served" -gt 0 ] && break
    sleep 5
  done
  verdict="replica 2: ready; it had served $served requests at $(date -u +%FT%TZ)"
else
  verdict="replica 2: NOT ready (pod ${second:-not created}, phase $(kubectl get -n vllm "${second:-pod/none}" -o jsonpath='{.status.phase}' 2>/dev/null || echo none)); see scaleout-events.json and gpu-group-errors.txt"
fi
echo "$verdict" | tee "$out/verdict.txt"
for f in "$out"/*; do sed -i.bak "s/$project/<project>/g" "$f" && rm -f "$f.bak"; done
GCS_TOKEN=$(gcloud auth print-access-token) python3 "$here/upload.py" "$results" "$out"/*
jq -r 'select(.hpa != null) | "\(.t) queue=\(.queue) hpa desired=\(.hpa.desired) current=\(.hpa.current) pods=\(.pods | map("\(.phase)/\(.ready)") | join(","))"' \
  "$out/scaleout-series.jsonl" | tail -5
jq -r '.[] | "\(.last) \(.reason) \(.object): \(.message)"' "$out/scaleout-events.json" | tail -8
