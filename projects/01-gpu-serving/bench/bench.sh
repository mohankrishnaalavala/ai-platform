#!/usr/bin/env bash
# Benchmark the vLLM server in the session cluster from a Job inside the cluster (a port-forward
# would add latency and cap throughput), print its log, and leave the results in the results bucket.
# Usage (from the repo root; `make bench` runs it):
#   ZONE=us-central1-c [CONFIG=config/models/qwen2.5-7b-fp8.yaml] [SCENARIOS="chat rag"] \
#   [CONCURRENCY="1 8 16 24 32"] projects/01-gpu-serving/bench/bench.sh
# P2's gateway-overhead benchmark reuses it, with TARGET_URL and TARGET_MODEL (the gateway and its
# alias), KEY_SECRET (a Secret in this namespace whose OPENAI_API_KEY the client sends), BACKEND and
# ENDPOINT (the chat API), and RESULTS_PREFIX (where under the results bucket). Defaults: P1's run.
set -euo pipefail
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}

here=$(cd "$(dirname "$0")" && pwd)
config=${CONFIG:-config/models/qwen2.5-7b-bf16.yaml}
scenarios=${SCENARIOS:-chat rag}
concurrency=${CONCURRENCY:-1 8 16 24 32}
val() { awk -v k="$1:" '$1 == k {print $2; exit}' "$config"; }
name=$(val name); hf=$(val hfId); rev=$(val revision)
project=$(gcloud config get project 2>/dev/null)
owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
: "${name:?no name in $config}" "${hf:?no hfId in $config}" "${rev:?no revision in $config}" "${owner:?no owner label}" "${session:?no session label}"
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }
[[ "$scenarios" =~ ^[a-z\ ]+$ && "$concurrency" =~ ^[0-9\ ]+$ ]] || { echo "SCENARIOS must be words, CONCURRENCY numbers"; exit 1; }

# Benchmark only the config that is actually being served, once it is ready.
served=$(kubectl get deployment vllm -n vllm -o jsonpath='{.metadata.labels.model-config}')
[ "$served" = "$name" ] || { echo "vLLM serves '$served', not '$name': deploy that config first"; exit 1; }
kubectl rollout status deployment/vllm -n vllm --timeout=40m

vllm_pod() { kubectl get pods -n vllm -l app.kubernetes.io/name=vllm --field-selector=status.phase=Running \
  -o jsonpath='{.items[0].metadata.uid} {.items[0].spec.nodeName} {.items[0].status.containerStatuses[?(@.name=="vllm")].restartCount}'; }
before=$(vllm_pod)
node=$(cut -d' ' -f2 <<< "$before")
node_json=$(kubectl get node "$node" -o json)
machine=$(jq -r '.metadata.labels["node.kubernetes.io/instance-type"]' <<< "$node_json")
gpu=$(jq -r '.metadata.labels["cloud.google.com/gke-accelerator"]' <<< "$node_json")
spot=$(jq -r '.metadata.labels["cloud.google.com/gke-spot"] // "false"' <<< "$node_json")
gke=$(jq -r '.status.nodeInfo.kubeletVersion' <<< "$node_json")
gpu_zone=$(jq -r '.metadata.labels["topology.kubernetes.io/zone"]' <<< "$node_json")
# The client's zone too: GPU nodes may start in another zone than the system node, which adds latency.
client_zone=$(kubectl get nodes -l cloud.google.com/gke-nodepool="${BENCH_POOL:-system}" \
  -o jsonpath='{.items[0].metadata.labels.topology\.kubernetes\.io/zone}')
image=$(kubectl get deployment vllm -n vllm -o jsonpath='{.spec.template.spec.containers[0].image}')
# Only the digest goes into the results: the registry path contains the project ID.
digest=${image##*@}
[[ "$digest" == sha256:* ]] || { echo "the vLLM image isn't pinned by digest"; exit 1; }
hash=$(python3 "$here/../../../scripts/config_hash.py" "$config")
results="gs://$project-results/${RESULTS_PREFIX:-p1/$hash/$session/bench}/$(date -u +%Y%m%dt%H%M%Sz)"
target=${TARGET_URL:-http://vllm.vllm.svc:8000}
meta="config=$name config_hash=$hash revision=$rev session=$session machine=$machine gpu=$gpu gpu_zone=$gpu_zone client_zone=$client_zone spot=$spot vllm_image=$digest gke=$gke${TARGET_URL:+ target=${TARGET_URL#http://}}"
labels="app=ai-platform layer=01 owner=$owner session=$session"

cleanup() { kubectl delete job bench -n vllm --ignore-not-found --wait=false; kubectl delete configmap bench-scripts -n vllm --ignore-not-found; }
trap cleanup EXIT
kubectl delete job bench -n vllm --ignore-not-found --wait
# shellcheck disable=SC2086  # $labels is a list of key=value words
kubectl create configmap bench-scripts -n vllm --from-file="$here/run.sh" --from-file="$here/summary.py" \
  --from-file="$here/upload.py" --dry-run=client -o yaml | kubectl label --local -f - $labels -o yaml | kubectl apply -f -
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: bench
  namespace: vllm
  labels: {app: ai-platform, layer: "01", owner: "$owner", session: "$session"}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 7200  # never longer than 2 hours
  template:
    metadata:
      labels: {app: ai-platform, layer: "01", owner: "$owner", session: "$session"}
    spec:
      serviceAccountName: bench
      restartPolicy: Never
      enableServiceLinks: false  # else VLLM_PORT=tcp://... breaks vLLM's CLI
      # The system node, so the client doesn't take CPU from the server it measures.
      nodeSelector:
        cloud.google.com/gke-nodepool: ${BENCH_POOL:-system}
      tolerations:
        - {key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}
      containers:
        - name: bench
          image: "$image"
          command: [bash, /bench/run.sh]
          env:
            - {name: VLLM_URL, value: "$target"}
            - {name: TARGET_MODEL, value: "${TARGET_MODEL:-$hf}"}
            - {name: BACKEND, value: "${BACKEND:-openai}"}
            - {name: ENDPOINT, value: "${ENDPOINT:-/v1/completions}"}
            - {name: HF_ID, value: "$hf"}
            - {name: REVISION, value: "$rev"}
            - {name: SCENARIOS, value: "$scenarios"}
            - {name: CONCURRENCY, value: "$concurrency"}
            - {name: RESULTS, value: "$results"}
            - {name: META, value: "$meta"}
            - {name: VLLM_NO_USAGE_STATS, value: "1"}
            - {name: HF_HUB_DISABLE_TELEMETRY, value: "1"}
$([ -z "${KEY_SECRET:-}" ] || printf '          envFrom:\n            - secretRef: {name: %s}  # OPENAI_API_KEY, never an argument (arguments are logged)\n' "$KEY_SECRET")
          resources:
            requests: {cpu: "1", memory: 2Gi}
          volumeMounts:
            - {name: scripts, mountPath: /bench}
      volumes:
        - {name: scripts, configMap: {name: bench-scripts}}
EOF

# Wait for the pod to start (the first pull of the image on this node), then follow its log.
for _ in $(seq 120); do
  phase=$(kubectl get pods -n vllm -l job-name=bench -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)
  case $phase in Running|Succeeded|Failed) break ;; esac
  sleep 10
done
raw=$(mktemp -d)
kubectl logs -n vllm -f job/bench | tee "$raw/job-log.txt" || true
# succeeded/failed counts; "/" until the Job controller has recorded the outcome.
st=/
for _ in $(seq 30); do
  st=$(kubectl get job bench -n vllm -o jsonpath='{.status.succeeded}/{.status.failed}')
  [ "$st" != / ] && break
  sleep 2
done

# The raw log goes next to the results. If the vLLM pod was replaced or restarted during the run
# (a spot interruption), every number from this run is invalid.
export GCS_TOKEN; GCS_TOKEN=$(gcloud auth print-access-token)
after=$(vllm_pod || echo gone)  # no Running vLLM pod at all is the clearest interruption
[ "$after" = "$before" ] \
  || echo "vLLM pod changed during the run (before: $before; after: $after): spot interruption? Re-run." | tee "$raw/INVALID.txt"
python3 "$here/upload.py" "$results" "$raw"/*
[ "$st" = 1/ ] || { echo "the benchmark Job failed (succeeded/failed: $st)"; exit 1; }
[ ! -f "$raw/INVALID.txt" ] || exit 1
echo "results uploaded to the results bucket: ${results#gs://*/}/"
