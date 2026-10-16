#!/usr/bin/env bash
# P0 checks against the running session cluster (`make up` first). Read-only.
# Uses the gcloud configuration and kubeconfig the Makefile exports.
set -euo pipefail

fail=0
check() { if [ -n "$2" ]; then echo "FAIL: $1: $2"; fail=1; else echo "PASS: $1"; fi; }

nodes=$(kubectl get nodes --no-headers)
check "at least one node" "$([ -n "$nodes" ] || echo "no nodes")"
check "every node is Ready" "$(awk '$2 != "Ready" {print $1}' <<< "$nodes")"

# System images come in through Private Google Access, so this also proves nodes need no NAT.
# Each command's output is captured first, so a failing kubectl or gcloud stops the script
# instead of looking like an empty, passing result.
pods=$(kubectl get pods -n kube-system --no-headers)
check "every kube-system pod is Running or Completed" \
  "$(awk '$3 != "Running" && $3 != "Completed" {print $1 " " $3}' <<< "$pods")"

vms=$(gcloud compute instances list --format='value(name,networkInterfaces[0].accessConfigs[0].natIP)')
check "no VM has an external IP" "$(awk 'NF > 1' <<< "$vms")"

ip=$(gcloud container clusters describe ai-platform --location="${ZONE:-us-central1-c}" \
  --format='value(controlPlaneEndpointsConfig.ipEndpointsConfig.enabled)' | tr '[:upper:]' '[:lower:]')
check "control plane: IP endpoints off" "$([ "$ip" = false ] || echo "enabled=${ip:-unset}")"

sas=$(gcloud iam service-accounts list --format='value(email)')
keys=""
for sa in $sas; do
  k=$(gcloud iam service-accounts keys list --iam-account="$sa" --managed-by=user --format='value(name)')
  keys+=$k
done
check "no user-managed service account keys" "$keys"

if [ $fail -ne 0 ]; then echo "some P0 checks failed"; exit 1; fi
echo "all P0 checks passed"
