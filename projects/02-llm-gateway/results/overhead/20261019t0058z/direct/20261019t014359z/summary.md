# Benchmark summary

- Config: `qwen2.5-7b-fp8`, hash `5d6172c8cb52818f`, model revision `a09a35458c702b33eeacc393d103063234e8bc28`
- Session: 20261019t0058z, date: 20261019-014946
- Hardware: g2-standard-8, 1x nvidia-l4, spot: false; GPU zone us-central1-c, client zone us-central1-c
- Software: vLLM 0.30.0 (image `sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40`), GKE v1.36.4-gke.1495002
- Method: `vllm bench serve` as a Job inside the cluster; random prompts of fixed length (chat = 512 tokens in / 128 out); prompts = max(20, 10 x concurrency) after max(2, concurrency) warm-up requests; seed 0

| Scenario | Concurrency | Requests ok / failed | TTFT p50 / p95 / p99 (ms) | TPOT p50 (ms) | Output tokens/s | Valid |
|---|---|---|---|---|---|---|
| chat | 10 | 100 / 0 | 706 / 890 / 978 | 38.3 | 230.3 | yes |
| chat | 50 | 500 / 0 | 1078 / 2320 / 3902 | 72.7 | 663.7 | yes |
