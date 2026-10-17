# Benchmark summary

- Config: `qwen2.5-7b-bf16`, hash `897bd98c858d1477`, model revision `a09a35458c702b33eeacc393d103063234e8bc28`
- Session: 20261017t1120z, date: 20261017-125750
- Hardware: g2-standard-8, 1x nvidia-l4, spot: false; GPU zone us-central1-a, client zone us-central1-c
- Software: vLLM 0.30.0 (image `sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40`), GKE v1.36.4-gke.1495002
- Method: `vllm bench serve` as a Job inside the cluster; random prompts of fixed length (chat = 512 tokens in / 128 out; rag = 4000 tokens in / 256 out); prompts = max(20, 10 x concurrency) after max(2, concurrency) warm-up requests; seed 0

| Scenario | Concurrency | Requests ok / failed | TTFT p50 / p95 / p99 (ms) | TPOT p50 (ms) | Output tokens/s | Valid |
|---|---|---|---|---|---|---|
| chat | 1 | 20 / 0 | 170 / 173 / 174 | 56.8 | 17.3 | yes |
| chat | 8 | 80 / 0 | 699 / 1100 / 1105 | 60.5 | 120.5 | yes |
| chat | 16 | 160 / 0 | 270 / 1700 / 2244 | 61.4 | 228.1 | yes |
| chat | 24 | 240 / 0 | 1371 / 2202 / 3253 | 81.9 | 261.0 | yes |
| chat | 32 | 320 / 0 | 1467 / 2631 / 4319 | 90.0 | 316.2 | yes |
| rag | 1 | 20 / 0 | 1152 / 1157 / 1162 | 57.7 | 16.2 | yes |
| rag | 8 | 80 / 0 | 2319 / 4743 / 7275 | 90.0 | 81.2 | yes |
| rag | 16 | 160 / 0 | 6561 / 23355 / 32475 | 114.5 | 100.8 | yes |
| rag | 24 | 240 / 0 | 32743 / 32929 / 42229 | 114.8 | 99.3 | yes |
| rag | 32 | 320 / 0 | 48357 / 59178 / 66440 | 114.8 | 100.5 | yes |
