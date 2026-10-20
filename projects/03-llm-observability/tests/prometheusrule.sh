#!/usr/bin/env bash
# Print one rule file (slos/*.rules.yaml: Prometheus rule groups) as a PrometheusRule for the operator.
# deploy.sh applies the result; tests/rules.sh validates it. Usage: OWNER=… SESSION=… prometheusrule.sh FILE
set -euo pipefail
f=${1:?usage: prometheusrule.sh FILE}
printf 'apiVersion: monitoring.coreos.com/v1\nkind: PrometheusRule\nmetadata:\n  name: %s\n  namespace: monitoring\n' \
  "$(basename "$f" .rules.yaml)"
printf '  labels: {app: ai-platform, layer: "03", owner: "%s", session: "%s"}\nspec:\n' "${OWNER:?}" "${SESSION:?}"
sed -e '/^---$/d' -e 's/^/  /' "$f"  # Sloth's file starts with a document marker
