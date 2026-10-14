# ai-platform (working name)

An open-source reference platform for running AI in production on Google Cloud:

- self-hosted models on GPUs
- one gateway for every LLM call
- monitoring and SLOs
- eval-gated model releases
- a safety gate for AI agents that change infrastructure

It's built in public, one layer at a time. Each layer is a standalone story an enterprise team can copy, and each one runs on Terraform + GKE. You **develop locally for free**, **spin up GCP only to test**, and **tear everything down after**.

![Platform overview](docs/diagrams/platform-overview.svg)

## The five projects (plus the base they stand on)

| # | Project | The enterprise problem it solves | Weeks | Status |
|---|---|---|---|---|
| 0 | [Foundation](projects/00-foundation/) | "Our GPU bill is huge because nobody turns anything off." | 1 | planned |
| 1 | [GPU model serving](projects/01-gpu-serving/) | "We want our own model, but we don't know what it costs or how fast it is." | 1–3 | planned |
| 2 | [LLM gateway](projects/02-llm-gateway/) | "Every team calls AI with its own keys, and nobody knows the spend." | 4–6 | planned |
| 3 | [LLM observability & SLOs](projects/03-llm-observability/) | "The AI feature is 'up', but users wait 8 seconds." | 7–8 | planned |
| 4 | [Eval-gated model releases](projects/04-eval-gated-releases/) | "We swapped the model, quality dropped, and nobody noticed for a week." | 9–11 | planned |
| 5 | [Agent action gateway](projects/05-agent-action-gateway/) | "We want AI agents to fix infrastructure, but we're scared they'll break prod." | 12–16 | planned |

## Two ways to run it

| Mode | What runs | Cost | Use it for |
|---|---|---|---|
| **Laptop** | kind (Kubernetes in Docker) + a tiny model via Ollama on CPU | Free | Daily development, CI on every pull request |
| **Cloud** | GKE zonal cluster + one NVIDIA L4 GPU (spot) | $[X] per session (not measured yet) | Benchmarks, demos, real numbers |

```bash
# Laptop
make local-up            # kind + Ollama + the layers built so far
make test P=02           # run project 2's tests
make local-down

# Cloud (one session = develop/test/terminate)
make up                  # Terraform creates the session cluster, then deploys the layers
make test P=01           # tests + benchmarks; results saved to projects/01-*/results and GCS
make down                # deletes everything except the cheap foundation
make status              # proves nothing billable is still running
```

> Those `make` targets are the plan. They get built in [Project 0](projects/00-foundation/).
