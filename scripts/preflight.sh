#!/usr/bin/env bash
# make preflight: read-only checks before `make up` for the P4 + P5 cluster window. Each check prints OK or
# MISSING, and the exit code is 1 if anything is missing. It runs as you (the ai-platform gcloud configuration,
# and gh), lists metadata only, never reads a secret's value, and creates, changes or deletes nothing.
# gcloud's own messages are dropped, since they can name the account.
# shellcheck disable=SC2329  # the check functions run through check(), which shellcheck can't follow
set -uo pipefail
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1
cd "$(dirname "$0")/.." || exit 1
project=$(gcloud config get project 2>/dev/null) || project=
registry="us-central1-docker.pkg.dev/$project/images"
# The configs the window serves: the BF16 baseline, FP8 (P4-07), the newer model and the bad candidate.
configs=(config/models/qwen2.5-7b-bf16.yaml config/models/qwen2.5-7b-fp8.yaml config/models/qwen3-8b.yaml
  config/models/qwen2.5-1.5b.yaml)
dataset=projects/04-eval-gated-releases/evals/datasets/v1
dataset_test=projects/04-eval-gated-releases/tests/test_golden_set.py
gateway_values=projects/05-agent-action-gateway/helm/action-gateway/values.yaml
missing=0

check() {  # check WHAT COMMAND...: OK when the command succeeds. Its output is dropped.
  local what=$1
  shift
  if "$@" >/dev/null 2>&1; then echo "OK       $what"; else echo "MISSING  $what"; missing=1; fi
}

val() { sed -n "s/^ *$2: *\([^ #]*\).*/\1/p" "$1" | head -1; }  # val FILE KEY: one value from a model config

labelled() { [ -n "$project" ] && [ "$(gcloud projects describe "$project" --format='value(labels.app)')" = ai-platform ]; }

enabled_version() { [ -n "$(gcloud secrets versions list "$1" --filter=state=ENABLED --format='value(name)' --limit=1)" ]; }

image() { [[ "$(gcloud artifacts docker images describe "$1" --format='value(image_summary.digest)')" =~ ^sha256: ]]; }

# Everything vLLM loads, where mirror.yml copies it: config.json, the tokenizer config and every weight shard.
weights() {
  local path files n i f want="config.json tokenizer_config.json"
  path=$(val "$1" bucketPath)
  [ -n "$path" ] || return 1
  files=$(gcloud storage ls "gs://$project-models/$path/**") || return 1
  n=$(grep -oE -- '-of-[0-9]{5}\.safetensors$' <<< "$files" | head -1 | tr -dc 0-9)
  if [ -z "$n" ]; then
    want+=" model.safetensors"
  else
    for i in $(seq "$((10#$n))"); do want+=" $(printf 'model-%05d-of-%s.safetensors' "$i" "$n")"; done
  fi
  for f in $want; do grep -qxF "gs://$project-models/$path/$f" <<< "$files" || return 1; done
}

# The golden set as it is on main (no local change), with its counts, and its own test passing.
golden_set() {
  git fetch -q origin main && git diff --quiet FETCH_HEAD -- "$dataset" "$dataset_test" \
    && [ -z "$(git status --porcelain -- "$dataset" "$dataset_test")" ] && [ -f "$dataset_test" ] || return 1
  [ "$(cat "$dataset"/*.jsonl | python3 -c 'import collections, json, sys
c = collections.Counter(json.loads(x)["category"] for x in sys.stdin if x.strip())
print(" ".join(f"{k}={v}" for k, v in sorted(c.items())))')" = "instruction=20 qa=40 safety=20 structured=20" ] \
    && python3 -m unittest discover -s "${dataset_test%/*}" -p "${dataset_test##*/}"
}

raised() { [[ "$1" =~ ^[0-9]+$ ]] && [ "$((10#$1))" -gt 8 ]; }

check "gcloud project (configuration ai-platform) has the label app=ai-platform" labelled
for s in slack-app-token slack-bot-token; do
  check "Secret Manager secret $s has an ENABLED version (only versions listed, never the value)" enabled_version "$s"
done
for c in "${configs[@]}"; do
  check "weights for $c in the models bucket ($(val "$c" bucketPath))" weights "$c"
done
for digest in $(for c in "${configs[@]}"; do val "$c" digest; done | sort -u); do
  check "vLLM image vllm-openai@${digest:0:19}… in Artifact Registry" image "$registry/vllm-openai@$digest"
done
# The digest the chart pins, if it pins one; otherwise main, the tag P5's deploy.sh resolves to a digest.
pinned=$(sed -n 's/^ *ref: *[^ #]*@\(sha256:[0-9a-f]\{64\}\).*/\1/p' "$gateway_values" 2>/dev/null | head -1)
ref=${pinned:+@$pinned}
check "action gateway image action-gateway${ref:-:main} in Artifact Registry" image "$registry/action-gateway${ref:-:main}"
check "golden set v1 as on main: 100 cases (qa 40, structured 20, instruction 20, safety 20), its test passes" golden_set
hours=$(gh variable list --json name,value 2>/dev/null | jq -r '.[] | select(.name == "COST_GUARD_MAX_AGE_HOURS") | .value' 2>/dev/null)
check "repo variable COST_GUARD_MAX_AGE_HOURS raised above 8 for the window (now: ${hours:-unset})" raised "$hours"
check "make status: nothing billable is running (no cluster up yet)" make --no-print-directory status

if [ "$missing" = 0 ]; then echo "preflight: everything is in place for make up"; else echo "preflight: fix what is MISSING first"; fi
exit "$missing"
