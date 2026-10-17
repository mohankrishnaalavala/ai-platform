# Benchmark summary

- Config: `qwen2.5-7b-fp8`, hash `5d6172c8cb52818f`, model revision `a09a35458c702b33eeacc393d103063234e8bc28`
- Session: 20261017t0432z, date: 20261017-055429
- Hardware: g2-standard-8, 1x nvidia-l4, spot: true; GPU zone us-central1-a, client zone us-central1-c
- Software: vLLM 0.30.0 (image `sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40`), GKE v1.36.4-gke.1495002
- Method: `vllm bench serve` as a Job inside the cluster; random prompts of fixed length (chat = 512 tokens in / 128 out; rag = 4000 tokens in / 256 out); prompts = max(20, 10 x concurrency) after max(2, concurrency) warm-up requests; seed 0

| Scenario | Concurrency | Requests ok / failed | TTFT p50 / p95 / p99 (ms) | TPOT p50 (ms) | Output tokens/s | Valid |
|---|---|---|---|---|---|---|
| chat | 1 | 20 / 0 | 113 / 117 / 117 | 34.7 | 28.3 | yes |
| chat | 8 | 80 / 0 | 476 / 768 / 891 | 37.3 | 195.8 | yes |
| chat | 16 | 160 / 0 | 192 / 1040 / 1453 | 38.2 | 364.3 | yes |
| chat | 24 | 240 / 0 | 179 / 1587 / 1991 | 39.9 | 516.4 | yes |
| chat | 32 | 320 / 0 | 180 / 1665 / 2079 | 42.0 | 657.0 | yes |
| rag | 1 | 20 / 0 | 868 / 883 / 886 | 35.6 | 25.8 | yes |
| rag | 8 | 80 / 0 | 1760 / 3105 / 3217 | 61.3 | 129.0 | yes |
| rag | 16 | 160 / 0 | 1895 / 6358 / 12324 | 96.8 | 154.3 | yes |
| rag | 24 | 240 / 0 | 2159 / 10166 / 19822 | 141.9 | 161.0 | yes |
| rag | 32 | 320 / 0 | 2394 / 13970 / 27830 | 193.6 | 159.6 | yes |
