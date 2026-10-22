# Pinned versions

Record every pinned version here when you add it (Terraform providers, GKE version, Helm charts, images, Python packages, models).

| Component | Version | Pinned in | Date |
|---|---|---|---|
| Terraform | 1.5.7 (last open-source MPL-2.0 release) | `infra/foundation/versions.tf` | 2026-10-15 |
| Google provider (`hashicorp/google`) | 8.7.0 | `infra/foundation/versions.tf`, `.terraform.lock.hcl` | 2026-10-15 |
| tflint (Docker image) | `ghcr.io/terraform-linters/tflint:v0.64.0@sha256:1c595f42…` | `ci.yml`; same command on the laptop | 2026-10-15 |
| shellcheck (Docker image) | `koalaman/shellcheck:v0.11.0@sha256:61862eba…` | `ci.yml`; same command on the laptop | 2026-10-15 |
| GKE (session cluster) | `1.36.4-gke.1495002`, REGULAR channel (the newest version all three us-central1 L4 zones offer; `1.36.4-gke.1391002` was already gone from -c) | `infra/session/main.tf` | 2026-10-15 |
| actionlint (Docker image) | `rhysd/actionlint:1.7.12@sha256:b1934ee5…` | `ci.yml` | 2026-10-15 |
| Trivy (Docker image; not `trivy-action`, whose tags were hijacked in March 2026) | `aquasec/trivy:0.75.0@sha256:af6acf9a…` | `ci.yml` | 2026-10-15 |
| `actions/checkout` | v7.0.1 (`3d3c42e5…`) | all workflows | 2026-10-15 |
| `hashicorp/setup-terraform` | v4.0.1 (`dfe3c3f8…`) | all workflows | 2026-10-15 |
| `google-github-actions/auth` | v3.0.0 (`7c6bc770…`) | `gpu-session.yml`, `cost-guard.yml`, `mirror.yml` | 2026-10-15 |
| `google-github-actions/setup-gcloud` | v3.0.1 (`aa5489c8…`), gcloud CLI 587.0.0 | `gpu-session.yml`, `cost-guard.yml`, `mirror.yml` | 2026-10-15 |
| ruff (Docker image) | `ghcr.io/astral-sh/ruff:0.16.9@sha256:fd4a00e8…` | `ci.yml` | 2026-10-16 |
| vLLM image | `vllm/vllm-openai:v0.30.0`, linux/amd64 manifest `sha256:5f5e5352…` (released 2026-09-22; v0.31.0 was 11 days old) | `config/models/*.yaml`; copied to Artifact Registry by `mirror.yml` | 2026-10-16 |
| Model | `Qwen/Qwen2.5-7B-Instruct`, revision `a09a3545…` (Apache 2.0, not gated) | `config/models/*.yaml` | 2026-10-16 |
| crane (go-containerregistry) | v0.22.1, release tarball checked by sha256 `0ab7a1d6…` | `mirror.yml` | 2026-10-16 |
| Helm | v4.3.0 (2026-09-09), release tarball checked by sha256 `86584a54…` | `ci.yml`, `release.yml` | 2026-10-16 |
| kubeconform (Docker image), Kubernetes schemas 1.36.0 | `ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43…` | `projects/0{1,2,3}-*/tests/charts.sh` | 2026-10-16 |
| kube-prometheus-stack (Helm chart, OCI) | 91.9.0 (2026-10-02; operator v0.94.1), chart digest `sha256:04047e50…` | `projects/01-gpu-serving/deploy.sh`, `tests/charts.sh` | 2026-10-16 |
| kube-prometheus-stack, P3's parts (same chart 91.9.0) | Grafana chart 13.2.7 (Grafana 13.2.3, AGPL-3.0), Alertmanager v0.34.1, kube-state-metrics chart 8.6.0; Prometheus v3.15.0 | `projects/03-llm-observability/deploy.sh`, `tests/charts.sh` | 2026-10-20 |
| Tempo (Helm chart, single binary) | `tempo` 3.1.0 (2026-09-30; Tempo 3.1.0, AGPL-3.0; chart Apache-2.0) from `https://grafana-community.github.io/helm-charts` (the chart moved there from `grafana/helm-charts`) | `projects/03-llm-observability/deploy.sh`, `tests/charts.sh` | 2026-10-20 |
| Tempo and Prometheus in CI (images) | `grafana/tempo:3.1.0@sha256:3076b8dc…`, `quay.io/prometheus/prometheus:v3.15.0-distroless@sha256:b2a413d5…` (the versions the charts run) | `projects/03-llm-observability/tests/compose.p3.yaml` | 2026-10-20 |
| OpenTelemetry Collector contrib (image, for `validate`) | `otel/opentelemetry-collector-contrib:0.161.0@sha256:fd328de2…` (P2's pin; the `span_metrics` and `sum` connectors and the `deltatocumulative` processor are alpha; the `sum` connector counts a value once per attribute, so P3 gives each sum one attribute) | `projects/03-llm-observability/tests/charts.sh` | 2026-10-20 |
| Sloth (CLI image, CI only) | `ghcr.io/slok/sloth:v0.16.0@sha256:f0f0075b…` (Apache-2.0): generates the SLO rules with `--default-slo-period=28d` | `projects/03-llm-observability/tests/rules.sh` | 2026-10-20 |
| promtool (Prometheus image, CI only) | `prom/prometheus:v3.15.0@sha256:efd719c9…` (the version the cluster runs) | `projects/03-llm-observability/tests/rules.sh`, `tests/charts.sh` | 2026-10-20 |
| Python (image, alert receiver) | `python:3.13-slim@sha256:70729b46…` (P2's mock-provider pin) | `projects/03-llm-observability/helm/monitoring.yaml` | 2026-10-20 |
| KEDA (Helm chart) | 2.21.0 (2026-09-23; supports Kubernetes 1.34–1.36) | `projects/01-gpu-serving/deploy.sh`, `tests/charts.sh` | 2026-10-16 |
| CRDs-catalog (schemas for kubeconform) | commit `d373c2da…` (2026-09-29) | `projects/0{1,2,3}-*/tests/charts.sh` | 2026-10-16 |
| `actions/upload-artifact` | v7.0.1 (`043fb46d…`, 2026-04-10; v7.0.2 was 9 days old) | `gpu-session.yml` | 2026-10-16 |
| LiteLLM proxy (image, open-source features only) | `ghcr.io/berriai/litellm-database:v1.103.3@sha256:11f25370…` (2026-10-03; tested with no license key: `projects/02-llm-gateway/results/spike-2026-10-18.md`) | `projects/02-llm-gateway/helm/gateway/values.yaml` | 2026-10-18 |
| Postgres (image) | `postgres:17.6-alpine@sha256:ef257d85…` | `projects/02-llm-gateway/helm/gateway/values.yaml` | 2026-10-18 |
| OpenTelemetry Collector (Helm chart) | `opentelemetry-collector` 0.174.2 (2026-10-01; app 0.161.0) | `projects/02-llm-gateway/deploy.sh`, `tests/charts.sh` | 2026-10-18 |
| OpenTelemetry Collector contrib (image) | `otel/opentelemetry-collector-contrib:0.161.0@sha256:fd328de2…` | `projects/02-llm-gateway/helm/otel-collector.yaml` | 2026-10-18 |
| Hosted model (Vertex AI, global endpoint) | `gemini-3.5-flash-lite` (`projects/02-llm-gateway/results/spike-2026-10-18.md`) | `projects/02-llm-gateway/helm/gateway/values.yaml` | 2026-10-18 |
| yq (Docker image) | `mikefarah/yq:4.54.1@sha256:4b3d9475…` (2026-09-29) | `ci.yml` (job gateway) | 2026-10-18 |
| Python (image for the mock provider) | `python:3.13-slim@sha256:70729b46…` | `projects/02-llm-gateway/tests/compose.yaml`, `helm/gateway/values.yaml` | 2026-10-18 |
| Presidio analyzer and anonymizer (images) | `mcr.microsoft.com/presidio-analyzer:2.2.362@sha256:286e3fa7…`, `presidio-anonymizer:2.2.362@sha256:a10a12a2…` (the newest on Microsoft's registry; 2.2.363 and 2.2.364 aren't published there). Inside: gunicorn 25.1.0 (unpinned in the image; seen in the anonymizer's log), run with `GUNICORN_CMD_ARGS=--no-control-socket` against its boot deadlock (gunicorn issues 3509 and 3529) | `projects/02-llm-gateway/helm/gateway/values.yaml`, `tests/compose.yaml` | 2026-10-18 |
| Our Helm charts (`gateway`, `vllm`) | 0.1.0, one version for all charts; `release.yml` pushes them to `oci://ghcr.io/mohankrishnaalavala/charts` on a tag (none yet) | `projects/*/helm/*/Chart.yaml` | 2026-10-18 |
| OPA (Open Policy Agent) image | `openpolicyagent/opa:1.21.1@sha256:d3a9a6bd…` (2026-09-29; v1.21.3 only fixes bundle signing, which P5 doesn't use) | `projects/05-agent-action-gateway/tests/policy.sh` (`opa test` and the scenario check in CI) | 2026-10-21 |
| pause image (P5 scenario fixtures: the demo `checkout` Deployments) | `registry.k8s.io/pause:3.10@sha256:ee6521f2…` | `projects/05-agent-action-gateway/evals/base.yaml`, `evals/scenarios/*.yaml` | 2026-10-21 |
