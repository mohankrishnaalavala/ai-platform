#!/usr/bin/env bash
# Package every Helm chart in projects/*/helm/ into DIR. Each chart's version must equal VERSION.
# release.yml passes the tag without its "v"; CI passes the gateway chart's version, so all charts must match.
set -euo pipefail
version=${1:?usage: package_charts.sh VERSION DIR} dir=${2:?usage: package_charts.sh VERSION DIR}

for c in "$(dirname "$0")"/../projects/*/helm/*/Chart.yaml; do
  grep -qxF "version: $version" "$c" || { echo "::error file=$c::the chart version isn't $version"; exit 1; }
  helm package "$(dirname "$c")" -d "$dir"
done
