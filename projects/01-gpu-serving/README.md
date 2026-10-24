# P1: GPU model serving

Qwen2.5-7B-Instruct served by vLLM on one NVIDIA L4 GPU (graphics processing unit) in the session cluster on GKE (Google Kubernetes Engine). The project measures its speed and cost, how fast a GPU replica starts from nothing, and whether it scales out under load.

**Status:** built and measured on 2026-10-17 in five `gpu-session.yml` runs from GitHub Actions: three short shakedown runs, then session 2 (the full run) and session 3 (a BF16 re-run). Four of the five session jobs ended red; the causes are listed in the teardown file. The numbers below come from the third shakedown run, session 2 and session 3. Every run ended with `make status` reporting "nothing billable is running" ([teardown output](results/teardown-2026-10-17.txt)). P4 measured FP8's answer quality against BF16 on 2026-10-23: no change beyond the run-to-run noise.

## What it runs

- **Serving:** vLLM 0.30.0 on a g2-standard-8 node (8 virtual CPUs, 32 GiB, 1x L4). It sits behind an internal-only (ClusterIP) Service, so there is no public IP or load balancer. The GPU node pool starts at 0 nodes.
- **Two model configs:** [`qwen2.5-7b-bf16.yaml`](../../config/models/qwen2.5-7b-bf16.yaml) serves the weights as published (BF16, 16-bit). [`qwen2.5-7b-fp8.yaml`](../../config/models/qwen2.5-7b-fp8.yaml) has vLLM compress them to FP8 (8-bit) while loading. The two files differ only in `name` and `quantization`. Each results folder is named by its config hash.
- **Fast start (the "optimized" path):** the image is mirrored into Artifact Registry and pulled with GKE image streaming. The weights come from the models bucket through Cloud Storage FUSE (the bucket mounted as a folder).
- **Slow start (the "baseline" path, for comparison only):** the image comes from Docker Hub and the weights from Hugging Face, both through Cloud NAT (network address translation), on a node pool without image streaming.
- **Autoscaling:** KEDA (Kubernetes Event-driven Autoscaling) scales between 1 and 2 replicas on vLLM's queue, `sum(vllm:num_requests_waiting)`, with a threshold of 5. CPU isn't used, because the GPU saturates while the CPU idles. Prometheus (kube-prometheus-stack) scrapes vLLM.

Versions are pinned in [`docs/versions.md`](../../docs/versions.md).

## Results

Every number below comes from a file in [`results/`](results/). The files are named `<config hash>/<session>/<kind>/<time>/`.

Terms used below:
- **Scenarios:** "chat" is a short prompt with a short answer. "rag" (retrieval-augmented generation) is a long prompt with a longer answer.
- **p50 / p95:** the median, and the value 95% of requests beat.
- **Spot:** a cheaper GPU node that Google can take back at any time. On-demand nodes aren't taken back.
- **Cost columns:** each cost is computed for both spot and on-demand prices from the measured throughput. BF16 ran on an on-demand node and FP8 on a spot node, so in each row one of the two columns uses the price of a node type that wasn't used for that run.

| Measure | BF16 | FP8 |
|---|---|---|
| TTFT (time to first token) p50 / p95, chat, 8 concurrent | 699 / 1100 ms | 476 / 768 ms |
| Output tokens/s, 32 concurrent: chat / rag | 316.2 / 100.5 | 657.0 / 159.6 |
| $ per 1M output tokens, GPU node only, chat: spot / on-demand | $0.45 / $0.75 | $0.22 / $0.36 |
| $ per 1M output tokens, GPU node only, rag: spot / on-demand | $1.41 / $2.36 | $0.89 / $1.49 |
| Highest tested concurrency with p95 TTFT ≤ 2 s: chat / rag | 16 / 1 | 32 (the highest level tested) / 1 |
| Quality change vs BF16, P4's 100 eval cases, run 1 / run 2 | Q&A judge 4.625 / 4.625; structured 100% / 100%; safety 85% / 85%; instructions 85% / 85% | Q&A judge 4.65 / 4.575; structured 100% / 100%; safety 85% / 80%; instructions 85% / 85%: no change beyond the noise band, two runs each ([P4 results](../04-eval-gated-releases/README.md#results)) |

| Cold start, median of 3 runs | Baseline | Optimized |
|---|---|---|
| Node ready | 93 s | 90 s |
| Image pulled | 235 s | 6 s |
| Model loaded / first token | 245 s / 2.5 s | 299 s / 2.9 s |
| **Total** | **571.1 s (9.5 min)** | **402.3 s (6.7 min)** |

Each row is the median of its own phase. The medians don't add up to the median total.

### Speed

Sources: [BF16 summary](results/897bd98c858d1477/20261017t1120z/bench/20261017t115245z/summary.md) and [FP8 summary](results/5d6172c8cb52818f/20261017t0432z/bench/20261017t051412z/summary.md), with the raw `vllm bench serve` JSON next to each.

- **Method:** `vllm bench serve` runs as a Job inside the cluster, using random prompts of fixed length. Chat is 512 tokens in and 128 out. Rag is 4000 tokens in and 256 out. Each level sends max(20, 10 × concurrency) prompts after the warm-up requests. The concurrency levels are 1, 8, 16, 24 and 32. All 20 runs are valid, with 0 failed requests.
- **Hardware:** both ran on g2-standard-8 with one L4, the GPU in us-central1-a and the client in us-central1-c. FP8 ran on a spot node (session 2). BF16 ran on an on-demand node (session 3).
- **FP8 throughput:** about twice BF16's for chat (657.0 against 316.2 output tokens/s at 32 concurrent) and 1.6 times for rag (159.6 against 100.5).
- **Rag saturates early:** BF16 throughput is flat from 16 concurrent (100.8, 99.3, 100.5 tokens/s), while its TTFT p50 grows from 6.6 s to 48.4 s. That is consistent with requests waiting in the queue; the benchmark doesn't record the queue length. FP8 flattens from 16 concurrent too (154.3 to 161.0).
- **TPOT (time per output token) p50 for chat:** BF16 56.8 to 90.0 ms; FP8 34.7 to 42.0 ms, as concurrency rises from 1 to 32.

### Cost

Source: [`results/cost-2026-10-17.md`](results/cost-2026-10-17.md), the output of [`bench/cost.py`](bench/cost.py) on the four 32-concurrent runs, with prices from [`bench/prices.json`](bench/prices.json).

- **What's counted:** all prices are list prices in USD, read on 2026-10-17, before any credits or discounts. The headline counts the GPU node only.
- **All-in:** adding the system node and Cloud NAT's hourly charge gives, for example, FP8 chat $0.28 (spot) and $0.42 (on-demand) per 1M output tokens. It still leaves out NAT data per GiB, the GKE management fee and disks.
- **Break-even** is the share of the measured full load the node must carry, every hour it runs, to cost less than a hosted API for the same traffic. Below that share the API is cheaper; above 100% it always is. This compares prices only, not answer quality.

| Run | Together AI, Qwen2.5 7B Instruct Turbo: spot / on-demand | Claude Haiku 5.5: spot / on-demand |
|---|---|---|
| BF16 chat | 30% / 50% | 50% / 83% |
| BF16 rag | 28% / 47% | 68% / 114% |
| FP8 chat | 14% / 24% | 24% / 40% |
| FP8 rag | 18% / 30% | 43% / 72% |

Together's "Turbo" variant is not shown to be the same setup: its precision was not checked.

### Cold start

Sources: [optimized runs](results/897bd98c858d1477/20261017t0432z/coldstart/20261017t061229z/) and [baseline runs](results/897bd98c858d1477/20261017t0432z/coldstart/20261017t065142z/), BF16 on spot nodes, session 2. [`bench/coldstart.sh`](bench/coldstart.sh) scales vLLM to 0 and waits until no GPU node is left. It then asks for one replica and times each phase from Kubernetes timestamps ([`bench/timings.py`](bench/timings.py)).

| Path | Run | Node ready | Image pulled | Model loaded | First token | Total | GPU zone |
|---|---|---|---|---|---|---|---|
| optimized | 1 | 878 s | 6 s | 299 s | 2.9 s | 1185.9 s | us-central1-b |
| optimized | 2 | 90 s | 5 s | 298 s | 1.9 s | 394.9 s | us-central1-a |
| optimized | 3 | 77 s | 12 s | 309 s | 4.3 s | 402.3 s | us-central1-a |
| baseline | 1 | 74 s | 235 s | 245 s | 2.2 s | 556.2 s | us-central1-a |
| baseline | 2 | 93 s | 228 s | 247 s | 3.1 s | 571.1 s | us-central1-a |
| baseline | 3 | 117 s | 242 s | 243 s | 2.5 s | 604.5 s | us-central1-a |

- **Image streaming is where the time goes:** it cut the "image pulled" phase from 228–242 s to 5–12 s. With streaming, that phase ends at the pull event, and the image's files are read later, during "model loaded".
- **The optimized path loads more slowly.** "Model loaded" covers vLLM's start-up plus reading the weights, and on the optimized path it also includes reading the streamed image files. It took 298–309 s on the optimized path (weights from the bucket through Cloud Storage FUSE) against 243–247 s on the baseline (weights downloaded from Hugging Face, image already fully pulled). These runs don't separate the two causes. Either way, the bucket didn't make this phase faster.
- **Optimized run 1 waited 878 s for its node.** Its events show "3 in backoff after failed scale-up" at 06:24:32Z, and the scale-up was triggered only at 06:27:40Z ([events](results/897bd98c858d1477/20261017t0432z/coldstart/20261017t061229z/optimized-1-events.json)). The scale-out test just before it had hit the GPU quota, but the link between the two isn't proven. The run is valid and reported as measured.
- **Idle node removal:** the idle GPU node went away 105–120 s after the vLLM pod stopped (these six runs). The cluster autoscaler was on its OPTIMIZE_UTILIZATION profile. The one run on the default BALANCED profile, in another session, took 661 s ([shakedown](results/897bd98c858d1477/20261017t0256z/coldstart/20261017t033101z/removal-1.json)).

### Scale-out

Source: [`results/5d6172c8cb52818f/20261017t0432z/scaleout/20261017t055440z/`](results/5d6172c8cb52818f/20261017t0432z/scaleout/20261017t055440z/), FP8, sampled about every 5 s by [`bench/scaleout.sh`](bench/scaleout.sh).

- **Timing:** the load started at 05:54:43Z. The queue first passed the threshold at 05:57:24Z, with 116 requests waiting. The HPA (Horizontal Pod Autoscaler, driven by KEDA) asked for 2 replicas at 05:57:30Z.
- **Replica 2 never ran:** it stayed Pending until the run ended at 06:06:38Z, with `QUOTA_EXCEEDED: Quota 'PREEMPTIBLE_NVIDIA_L4_GPUS' exceeded. Limit: 1.0`. Going from 1 to 2 running replicas is not shown; KEDA asking for the second one is.

### Invalid run

Session 2's BF16 benchmark is [marked INVALID](results/897bd98c858d1477/20261017t0432z/bench/20261017t050146z/INVALID.txt), because the vLLM pod changed during the run. Compute Engine records two preemptions of the spot GPU node, at 05:03:01Z and 05:06:54Z ([operations](results/897bd98c858d1477/20261017t0432z/bench/20261017t050146z/gpu-node-operations.json)). BF16 was re-run on an on-demand node in session 3.

## How to run it

The usual way is the `gpu-session.yml` workflow (`mode`: shakedown, full or bf16-bench; `zone`; `spot`). See [`.github/workflows/README.md`](../../.github/workflows/README.md). It runs the steps below, down to `make status`, and always tears down at the end.

```bash
make up LAYERS=01          # session cluster + Prometheus, KEDA and vLLM with the BF16 config (deploy.sh)
make test P=01             # smoke tests, KEDA and Prometheus checks
make bench                 # BF16 matrix; results go to the results bucket
export ZONE=us-central1-c CONFIG=config/models/qwen2.5-7b-fp8.yaml
projects/01-gpu-serving/deploy.sh && make bench        # switch vLLM to FP8, then the FP8 matrix
projects/01-gpu-serving/bench/scaleout.sh
export CONFIG=config/models/qwen2.5-7b-bf16.yaml
projects/01-gpu-serving/deploy.sh
projects/01-gpu-serving/bench/coldstart.sh optimized 3
projects/01-gpu-serving/bench/coldstart.sh baseline 3  # leaves vLLM on the baseline path; run it last
make down && make status   # must say "nothing billable is running"
python3 -I projects/01-gpu-serving/bench/cost.py projects/01-gpu-serving/bench/prices.json <the 32-concurrent JSONs>
```

Unit tests for the bench scripts, which CI also runs: `python3 -m unittest discover -s projects/01-gpu-serving/bench`.

## Folder map

| Path | What it is |
|---|---|
| `deploy.sh` | Installs Prometheus, KEDA and the vLLM chart for one model config. With `VLLM_ONLY=1` it upgrades only vLLM's release, so a model swap on a cluster with P3's monitoring leaves that monitoring alone (P4's offline evals) |
| `helm/` | Values for Prometheus and KEDA, and the vLLM chart (`values-baseline.yaml` switches to the baseline path) |
| `tests/` | `make test P=01`: smoke tests and the autoscaling checks; `charts.sh` for CI |
| `bench/` | Benchmark, cold-start and scale-out scripts, the cost calculator, prices and unit tests |
| `results/` | Raw results, including the invalid run |
