#!/usr/bin/env bash
# Runs inside the benchmark Job (bench.sh starts it): pins the tokenizer, runs `vllm bench serve`
# for each scenario and concurrency, writes summary.md, and uploads everything (create-only).
# Prompts are random tokens of a fixed length: speed depends on length, not content.
set -uo pipefail
out=/tmp/results
mkdir -p "$out"

# bench serve can't pin a tokenizer revision itself, so fetch the pinned files first.
python3 -c "
import os
from huggingface_hub import snapshot_download
snapshot_download(os.environ['HF_ID'], revision=os.environ['REVISION'], local_dir='/tmp/tok',
                  allow_patterns=['tokenizer*', 'vocab.json', 'merges.txt', 'config.json', 'generation_config.json'])
" || exit 1

# The version inside the image, recorded with every result (defaults change between releases).
v=$(python3 -c 'import vllm; print(vllm.__version__)') && [ -n "$v" ] || { echo "can't read the vLLM version"; exit 1; }
META="$META vllm_version=$v"

bad=0
for s in $SCENARIOS; do
  case $s in
    chat) in=512 len=128 ;;
    rag) in=4000 len=256 ;;
    *) echo "unknown scenario $s"; exit 1 ;;
  esac
  for c in $CONCURRENCY; do
    # Enough prompts for stable percentiles without making concurrency 1 take forever.
    n=$(( c * 10 > 20 ? c * 10 : 20 )); w=$(( c > 2 ? c : 2 ))
    echo "== $s, concurrency $c: $n prompts after $w warm-up requests"
    # META holds key=value pairs without spaces; splitting it into words is intended.
    # shellcheck disable=SC2086
    vllm bench serve --base-url "$VLLM_URL" --model "$HF_ID" --tokenizer /tmp/tok \
      --dataset-name random --random-input-len "$in" --random-output-len "$len" --random-range-ratio 0 --ignore-eos \
      --num-prompts "$n" --max-concurrency "$c" --request-rate inf --num-warmups "$w" --seed 0 \
      --percentile-metrics ttft,tpot,itl,e2el --metric-percentiles 50,95,99 --ready-check-timeout-sec 600 \
      --disable-tqdm --save-result --result-dir "$out" --result-filename "$s-c$c.json" \
      --metadata scenario="$s" $META || { echo "FAILED: $s, concurrency $c"; bad=1; }
  done
done

if python3 /bench/summary.py "$out" "$SCENARIOS" "$CONCURRENCY" > "$out/summary.md"; then cat "$out/summary.md"; else bad=1; fi
# A marker next to the results, so a partial run can't pass for a complete one.
[ $bad -eq 0 ] || echo "at least one run failed or is missing; see summary.md and the Job log" > "$out/INCOMPLETE"
python3 /bench/upload.py "$RESULTS" "$out"/* || bad=1
exit $bad
