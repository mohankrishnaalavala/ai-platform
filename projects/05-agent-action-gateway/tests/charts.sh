#!/usr/bin/env bash
# Static checks for the action gateway chart, no cluster needed (CI runs every projects/*/tests/charts.sh):
# lint and render it as the cluster runs it (Secret Manager, Slack, GCS audit) and as kind runs it,
# validate both against the Kubernetes 1.36 schemas, and fail on:
# - any Service, Ingress, host access, disk (PVC) or Kubernetes Secret in the chart
# - an image not pinned by digest (in the cluster render), or the test-only clock
# - an object or pod without the labels app=ai-platform, layer=05, owner and session
# - what looks like a token
# and check that the chart refuses a tag-only image, a raw agent token and missing policies.
set -euo pipefail
cd "$(dirname "$0")/.."

chart=helm/action-gateway
kubeconform=ghcr.io/yannh/kubeconform:v0.8.0@sha256:faffaf43f95aa6425306e1ab8d6fcad72acb9049158f38e574c085ea1ec0f64e
yq=mikefarah/yq:4.54.1@sha256:4b3d9475d65571d28cbb19544d3820ec2945e4c8b2f18279394282b8dc3a592e
# Schemas for the custom resources (SecretProviderClass), pinned to a commit.
crds='https://raw.githubusercontent.com/datreeio/CRDs-catalog/d373c2da9702bc9509a004db83e57263fe3bdfc1/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
hash=$(printf '%s' placeholder-token | openssl dgst -sha256 | sed 's/^.*= //')
policies=(--set-file 'policies.gateway\.rego=policies/gateway.rego' --set-file 'policies.data\.json=policies/data.json')
# Placeholders for what deploy.sh reads from the cloud and the user's Slack workspace.
cloud=(--set project=p --set labels.owner=o --set labels.session=20261018t0000z --set audit.bucket=p-audit
       --set image.ref=us-central1-docker.pkg.dev/p/images/action-gateway@sha256:0000000000000000000000000000000000000000000000000000000000000000
       --set slack.channel=C0123ABCD --set-json 'slack.approvers={"default":["U0123ABCD"],"break-glass":["U0456EFGH"]}'
       --set-json "agentTokens={\"$hash\":\"payments\"}" "${policies[@]}")
kind=(-f tests/kind/values.yaml --set-json "agentTokens={\"$hash\":\"payments\"}" "${policies[@]}")
out=$(mktemp) && kind_out=$(mktemp)
trap 'rm -f "$out" "$kind_out"' EXIT

helm lint "$chart" "${cloud[@]}"
helm template action-gateway "$chart" -n action-gateway "${cloud[@]}" > "$out"
helm template action-gateway "$chart" -n action-gateway "${kind[@]}" > "$kind_out"
{ cat "$out"; echo ---; cat "$kind_out"; } | docker run --rm -i "$kubeconform" -strict -kubernetes-version 1.36.0 \
  -schema-location default -schema-location "$crds" -summary -

if grep -nE 'kind: (Service|Ingress|PersistentVolumeClaim|Secret)$|hostNetwork: true|hostPort:|hostPath:|hostPID: true|hostIPC: true' "$out" "$kind_out"; then
  echo "the chart renders a Service, an ingress, host access, a disk (PVC) or a Kubernetes Secret"; exit 1
fi
if grep -n 'TEST_ONLY' "$out" "$kind_out"; then
  echo "the chart sets the test-only clock; only tests may (kind-smoke.sh)"; exit 1
fi
if grep -nE 'xox[abp]-|xapp-|sk-[A-Za-z0-9]{8,}' "$out" "$kind_out"; then
  echo "a rendered manifest holds what looks like a token"; exit 1
fi
if grep -E '^ *image: ' "$out" | grep -v '@sha256:[0-9a-f]\{64\}"\?$'; then
  echo "an image in the cluster render isn't pinned by digest"; exit 1
fi
{ cat "$out"; echo ---; cat "$kind_out"; } | docker run --rm -i "$yq" ea -o=json -I=0 'select(. != null)' | python3 -c '
import json, sys
bad = []
for line in sys.stdin:
    o = json.loads(line)
    metas = [("", o["metadata"])]
    if "template" in o.get("spec", {}):
        metas.append((" (pod)", o["spec"]["template"]["metadata"]))
    for where, meta in metas:
        l = meta.get("labels") or {}
        if not (l.get("app") == "ai-platform" and l.get("layer") == "05" and l.get("owner") and l.get("session")):
            bad.append(o["kind"] + "/" + o["metadata"]["name"] + where)
sys.exit("missing labels (app, layer=05, owner, session): " + ", ".join(bad) if bad else 0)'

refuse() {  # refuse WHAT ARGS...: the render must fail
  local what=$1; shift
  if helm template action-gateway "$chart" -n action-gateway "$@" >/dev/null 2>&1; then
    echo "the chart accepted $what"; exit 1
  fi
}
refuse "an image by tag" "${cloud[@]}" --set image.ref=us-central1-docker.pkg.dev/p/images/action-gateway:main
refuse "a raw agent token" "${cloud[@]}" --set-json 'agentTokens={"my-secret-token":"payments"}'
refuse "no policies" -f tests/kind/values.yaml
refuse "the Slack approver with no approvers" "${cloud[@]}" --set-json 'slack.approvers={"default":[]}'
refuse "a Slack name instead of a user ID" "${cloud[@]}" --set-json 'slack.approvers={"default":["alice"]}'
echo "charts OK"
