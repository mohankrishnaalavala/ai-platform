#!/usr/bin/env bash
# make evals: the golden set (evals/run.py) against one gateway alias of the running session, from the laptop.
# P3's tests/lib.sh opens the port-forward to the gateway and reads its master key as the deployer, into memory
# only, as P2's tests/run.sh and make team do. The results go to results/<config hash>/, ready to commit.
# - Noise band: RUNS (default 2) separate runs, so two results files. Commit both: after a change to the gate,
#   gate.py's baseline is an earlier run of the same config, which may come in the same pull request.
# - REPEAT passes to run.py's --repeat: rounds inside one file (default 1).
# - A run stops the loop when it is INVALID (run.py exits 1), so a broken setup doesn't cost a second run.
# Usage (from the repo root): ZONE=us-central1-c TARGET=chat-default CONFIG=config/models/qwen2.5-7b-bf16.yaml \
#   [REPEAT=1] [RUNS=2] projects/04-eval-gated-releases/scripts/evals.sh
set -euo pipefail
export KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}
here=$(cd "$(dirname "$0")" && pwd)
[ -n "${TARGET:-}" ] && [ -f "${CONFIG:-}" ] \
  || { echo "usage: make evals TARGET=chat-default CONFIG=config/models/qwen2.5-7b-bf16.yaml [REPEAT=1] [RUNS=2]"; exit 1; }
runs=${RUNS:-2}
[[ "$runs" =~ ^[1-9][0-9]*$ ]] || { echo "RUNS must be a whole number, 1 or more"; exit 1; }

# The results name CONFIG, and nothing else proves which model answered: vLLM answers to a fixed name (P1's
# chart). So a self-hosted config (it has gpu:) must be the one vLLM serves now. Swap it with
# CONFIG=… VLLM_ONLY=1 make deploy LAYERS=01, which touches only vLLM's release.
if grep -q '^gpu:' "$CONFIG"; then
  want=$(sed -n 's/^name: *//p' "$CONFIG")
  have=$(kubectl get deployment vllm -n vllm -o jsonpath='{.spec.template.metadata.labels.model-config}' 2>/dev/null) || true
  [ "$have" = "$want" ] \
    || { echo "vLLM serves '${have:-nothing}', not $want: run CONFIG=$CONFIG VLLM_ONLY=1 make deploy LAYERS=01 first"; exit 1; }
fi

# Before sourcing: lib.sh opens the port-forward and may exit after that (no master key, say).
trap 'declare -F stop_port_forwards >/dev/null && stop_port_forwards' EXIT
# shellcheck source=/dev/null  # P3's tests/lib.sh
source "$here/../../03-llm-observability/tests/lib.sh"
for n in $(seq "$runs"); do
  echo "run $n of $runs: $TARGET with $CONFIG"
  python3 "$here/../evals/run.py" --target "$TARGET" --config "$CONFIG" --repeat "${REPEAT:-1}"
done
