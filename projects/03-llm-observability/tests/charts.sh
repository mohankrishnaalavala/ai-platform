#!/usr/bin/env bash
# Static chart checks, no cluster needed (CI runs this on every pull request): render P1's
# kube-prometheus-stack release with P3's values on top, and the Tempo chart, as deploy.sh installs them.
# Validate them against the Kubernetes 1.36 schemas, and fail on anything that would expose a service,
# leave a disk behind, or put a Kubernetes Secret or a secret value in a chart. P2's OTel Collector with
# P3's values on top is rendered too, and its config goes through the collector's own validator.
set -euo pipefail
cd "$(dirname "$0")/.."

kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
yq=mikefarah/yq:4.54.1@sha256:4b3d9475d65571d28cbb19544d3820ec2945e4c8b2f18279394282b8dc3a592e
otelcol=otel/opentelemetry-collector-contrib:0.161.0@sha256:fd328de2552466ad78385e1b1289c3f2402b1c45f265b252aab1955b42845ac1
promtool=prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e
collector=(opentelemetry-collector --repo https://open-telemetry.github.io/opentelemetry-helm-charts --version 0.174.2
           -n gateway -f ../02-llm-gateway/helm/otel-collector.yaml -f helm/otel-collector.yaml)
# Schemas for the custom resources (Prometheus, Alertmanager, AlertmanagerConfig, ServiceMonitor, ScaledObject), pinned to a commit.
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/d373c2da9702bc9509a004db83e57263fe3bdfc1/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
out=$(mktemp) cfg=$(mktemp -d)
trap 'rm -rf "$out" "$cfg"' EXIT

# CRDs are left out, as in P1's checks: kubeconform has no schema for them, and their field
# descriptions would trip the grep below. owner and session are placeholders for what deploy.sh reads.
{ helm template prometheus oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack --version 91.9.0 -n monitoring \
    -f ../01-gpu-serving/helm/prometheus.yaml -f helm/monitoring.yaml --set crds.enabled=false \
    --set-string commonLabels.owner=o --set-string commonLabels.session=20261020t0000z; echo ---
  helm template tempo tempo --repo https://grafana-community.github.io/helm-charts --version 3.1.0 -n monitoring \
    -f helm/tempo.yaml; echo ---
  helm template otel-collector "${collector[@]}"; echo ---
  # P1's vLLM chart with P3's scale-from-zero values (P3-08), and the game day's NetworkPolicy (P3-09).
  helm template vllm ../01-gpu-serving/helm/vllm -n vllm -f ../../config/models/qwen2.5-7b-fp8.yaml \
    -f helm/vllm-scale-from-zero.yaml --set modelsBucket=p-models --set registry=r/p/images \
    --set labels.owner=o --set labels.session=20261020t0000z --show-only templates/scaledobject.yaml; echo ---
  cat tests/gameday/block-hosted.yaml; echo ---
  cat rules/cost.yaml; } >> "$out"

# The recording rules: taken out of the PrometheusRule, checked, and their math tested on made-up series.
docker run --rm -i "$yq" '.spec' < rules/cost.yaml > "$cfg/cost.rules.yaml"
cp tests/cost.rules.test.yaml "$cfg/"
docker run --rm --user "$(id -u)" --entrypoint /bin/promtool -v "$cfg:/cfg:ro" -w /cfg "$promtool" check rules cost.rules.yaml
docker run --rm --user "$(id -u)" --entrypoint /bin/promtool -v "$cfg:/cfg:ro" -w /cfg "$promtool" test rules cost.rules.test.yaml

# The collector's config, checked by the pinned collector itself (component names, pipelines). Its
# listen addresses use the pod's IP from the environment.
helm template otel-collector "${collector[@]}" --show-only templates/configmap.yaml \
  | docker run --rm -i "$yq" '.data.relay' > "$cfg/relay.yaml"
docker run --rm --user "$(id -u)" -e MY_POD_IP=0.0.0.0 -v "$cfg:/cfg:ro" "$otelcol" validate --config=/cfg/relay.yaml

# Every dashboard query must parse: wrap each as a recording rule and let promtool check them.
python3 - dashboards > "$cfg/dashboard-rules.yaml" <<'EOF'
import json, pathlib, sys
exprs = [(p.stem, t["expr"]) for p in sorted(pathlib.Path(sys.argv[1]).glob("*.json"))
         for panel in json.loads(p.read_text())["panels"] for t in panel["targets"]]
rules = [{"record": f"dashboard:{stem.replace('-', '_')}_{i}", "expr": e} for i, (stem, e) in enumerate(exprs)]
print(json.dumps({"groups": [{"name": "dashboards", "rules": rules}]}))  # JSON is YAML too
EOF
docker run --rm --user "$(id -u)" --entrypoint /bin/promtool -v "$cfg:/cfg:ro" "$promtool" check rules /cfg/dashboard-rules.yaml

docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 -schema-location default -schema-location "$crds" -summary - < "$out"
if grep -nE 'type: (LoadBalancer|NodePort|ExternalName)|kind: (Ingress|PersistentVolumeClaim|Secret)$|hostNetwork: true|hostPort: [0-9]|hostPath:' "$out"; then
  echo "a chart renders a public service, an ingress, host access, a disk (PVC) or a Kubernetes Secret"; exit 1
fi
# Grafana may read ConfigMaps in its own namespace only, so its sidecars must not watch every namespace.
if grep -n -A1 'name: NAMESPACE$' "$out" | grep -n 'value: "\?ALL'; then
  echo "a Grafana sidecar watches all namespaces, which its Role doesn't allow"; exit 1
fi
if grep -nE 'sk-[A-Za-z0-9]{8,}|password: ' "$out"; then
  echo "a rendered manifest holds what looks like a secret value"; exit 1
fi
echo "charts OK"
