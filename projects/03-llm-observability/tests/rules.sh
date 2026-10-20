#!/usr/bin/env bash
# SLO and alert rules, no cluster needed (CI runs this on every pull request):
# - the committed slos/platform.rules.yaml is exactly what Sloth generates from slos/platform.yaml (run this
#   script and commit the result after changing a spec)
# - every rule file passes promtool's checks
# - promtool's unit tests (tests/rules-test.yaml) show each alert firing at the predicted minute, and
#   staying quiet when it should; tests/cost-rules-test.yaml checks the cost rules' math
set -euo pipefail
cd "$(dirname "$0")/.."

sloth=ghcr.io/slok/sloth:v0.16.0@sha256:f0f0075b0d45c1cf684e92947508cc1d5bf573925f785f803a439b2306c8b9a5
prometheus=prom/prometheus:v3.15.0@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e
here=$(pwd)

docker run --rm -v "$here/slos:/slos:ro" "$sloth" generate -i /slos/platform.yaml --default-slo-period=28d --no-log \
  > slos/platform.rules.yaml
git diff --exit-code -- slos/platform.rules.yaml \
  || { echo "slos/platform.rules.yaml isn't what Sloth generates from slos/platform.yaml: run tests/rules.sh and commit"; exit 1; }
promtool=(docker run --rm -v "$here:/w:ro" -w /w --entrypoint /bin/promtool "$prometheus")
"${promtool[@]}" check rules slos/*.rules.yaml
"${promtool[@]}" test rules tests/rules-test.yaml tests/cost-rules-test.yaml
# As deploy.sh applies them: one PrometheusRule per file, checked against the operator's schema.
kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/d373c2da9702bc9509a004db83e57263fe3bdfc1/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
for f in slos/*.rules.yaml; do OWNER=o SESSION=20261020t0000z tests/prometheusrule.sh "$f"; echo ---; done \
  | docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 -schema-location default -schema-location "$crds" -summary -
echo "rules OK"
