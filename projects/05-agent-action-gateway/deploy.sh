#!/usr/bin/env bash
# Deploy P5 into the running session cluster. Needs P2 (layer 02) deployed first: the risk summary goes
# through its gateway, with a team key this script makes there.
#   1. the demo targets: evals/base.yaml exactly (dev, staging and prod, each with a 2-replica checkout
#      Deployment and a PodDisruptionBudget), plus the owner and session labels. Running this again puts
#      them back to that base state.
#   2. the action gateway's P2 team (action-gateway) and key, through P2's onboarding code, added as a new
#      version of the Secret Manager secret action-gateway-team-key (older versions destroyed). Once per
#      session: P2's database lives as long as the session, so a team that exists means this already ran.
#   3. an agent token for this session: printed once, only its SHA-256 hash stored (the agent-tokens
#      ConfigMap). Running this again keeps the existing tokens; NEW_AGENT_TOKEN=1 replaces them with a new one.
#   4. the chart (helm/action-gateway): RBAC, policies, the pod, the image mirror.yml pushed (by digest).
# Safe to run again.
#
# Usage (from the repo root): ZONE=us-central1-c projects/05-agent-action-gateway/deploy.sh
#   The Slack approver (the default) needs, from your own Slack workspace (slack.md), never from Git:
#     SLACK_CHANNEL_ID=C0123ABCD SLACK_APPROVERS=U0123ABCD[,U...] [SLACK_BREAK_GLASS_APPROVERS=U...]
#   and both Slack token secrets with a version. APPROVER=cli uses the CLI approver instead (kubectl exec).
#   AGENT_TEAM (default payments) is the team the new agent token maps to.
set -euo pipefail
# Same settings as the Makefile: never the machine's default project or kubeconfig.
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 KUBECONFIG=${KUBECONFIG:-$HOME/.kube/ai-platform}

here=$(cd "$(dirname "$0")" && pwd)
project=$(gcloud config get project 2>/dev/null)
owner=$(gcloud projects describe "$project" --format='value(labels.owner)')
session=$(gcloud container clusters describe ai-platform --location="${ZONE:?set ZONE}" --format='value(resourceLabels.session)')
[ -n "$owner" ] && [ -n "$session" ] || { echo "couldn't read the owner or session label"; exit 1; }
[ "$(kubectl config current-context)" = "gke_${project}_${ZONE}_ai-platform" ] \
  || { echo "kubectl doesn't point at this session's cluster (run make up first)"; exit 1; }
helm status gateway -n gateway >/dev/null 2>&1 \
  || { echo "P2 (layer 02) isn't deployed: run make up LAYERS=\"02 05\", or make deploy LAYERS=\"02 05\" on a running cluster"; exit 1; }

approver=${APPROVER:-slack} team=${AGENT_TEAM:-payments}
slack=(--set approver="$approver")
if [ "$approver" = slack ]; then
  [ -n "${SLACK_CHANNEL_ID:-}" ] && [ -n "${SLACK_APPROVERS:-}" ] \
    || { echo "the Slack approver needs SLACK_CHANNEL_ID and SLACK_APPROVERS (see slack.md), or run with APPROVER=cli"; exit 1; }
  for s in slack-app-token slack-bot-token; do
    [ -n "$(gcloud secrets versions list "$s" --filter=state=ENABLED --limit=1 --format='value(name)')" ] \
      || { echo "secret $s has no enabled version (or you can't list it): add the token (slack.md), or run with APPROVER=cli"; exit 1; }
  done
  approvers=$(jq -cn --arg d "$SLACK_APPROVERS" --arg b "${SLACK_BREAK_GLASS_APPROVERS:-}" \
    '{default: ($d | split(",") | map(select(. != ""))), "break-glass": ($b | split(",") | map(select(. != "")))}')
  slack+=(--set-string slack.channel="$SLACK_CHANNEL_ID" --set-json slack.approvers="$approvers")
fi
digest=$(gcloud artifacts docker images describe "us-central1-docker.pkg.dev/$project/images/action-gateway:main" \
  --format='value(image_summary.digest)' 2>/dev/null) || true
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "no action-gateway image in Artifact Registry: mirror.yml pushes it from main"; exit 1; }

# 1. The namespaces and the demo targets.
kubectl apply -f "$here/helm/namespace.yaml"
kubectl label namespace action-gateway owner="$owner" session="$session" --overwrite
OWNER=$owner SESSION=$session python3 -c '
import json, os, sys
objects = json.load(open(sys.argv[1]))["objects"]
for o in objects:
    o["metadata"]["labels"] |= {"owner": os.environ["OWNER"], "session": os.environ["SESSION"]}
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": objects}))' "$here/evals/base.yaml" | kubectl apply -f -

# 2. The P2 team key: the master key is read straight into the environment, never printed; the new key goes
# straight to Secret Manager.
kubectl port-forward -n gateway svc/gateway 14050:4000 >/dev/null & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
up=; for _ in $(seq 30); do curl -sf http://127.0.0.1:14050/health/liveliness >/dev/null && up=1 && break; sleep 1; done
[ -n "$up" ] || { echo "P2's gateway isn't reachable through port-forward on 127.0.0.1:14050"; exit 1; }
# As `make team` reads it: as the deployer (impersonated on the laptop; GitHub Actions already logs in as it).
as_deployer=(--impersonate-service-account="deployer@$project.iam.gserviceaccount.com")
[ -z "${GITHUB_ACTIONS:-}" ] || as_deployer=()
LITELLM_MASTER_KEY=$(gcloud secrets versions access latest --secret=litellm-master-key \
  ${as_deployer[@]+"${as_deployer[@]}"} 2>/dev/null) || true
[ -n "$LITELLM_MASTER_KEY" ] || { echo "couldn't read P2's master key"; exit 1; }
export LITELLM_MASTER_KEY GATEWAY_URL=http://127.0.0.1:14050
onboard='import os, sys
sys.path.insert(0, sys.argv[1])
import onboard_team as o
url, master = os.environ["GATEWAY_URL"], os.environ["LITELLM_MASTER_KEY"]
if sys.argv[2] == "exists":
    sys.exit(0 if any(t.get("team_alias") == "action-gateway" for t in o.admin(url, master, "GET", "/team/list")) else 1)
print(o.create_key(url, master, o.create_team(url, master, "action-gateway", 2, 30), "action-gateway-1"), end="")'
# If a run stops between making the team and adding the secret, delete the team in P2 and run this again.
if python3 -c "$onboard" "$here/../02-llm-gateway/scripts" exists; then
  echo "action-gateway-team-key: P2 team action-gateway already made this session; key kept"
else
  key=$(python3 -c "$onboard" "$here/../02-llm-gateway/scripts" create)
  [ -n "$key" ] || { echo "P2 gave no key for team action-gateway"; exit 1; }
  printf '%s' "$key" | gcloud secrets versions add action-gateway-team-key --data-file=- >/dev/null
  unset key
  latest=$(gcloud secrets versions list action-gateway-team-key --filter='state=ENABLED' --sort-by=~createTime --limit=1 --format='value(name.basename())')
  for old in $(gcloud secrets versions list action-gateway-team-key --filter='state!=DESTROYED' --format='value(name.basename())'); do
    [ "$old" = "$latest" ] || gcloud secrets versions destroy "$old" --secret=action-gateway-team-key --quiet >/dev/null
  done
  echo "action-gateway-team-key: new version $latest (P2 team action-gateway, \$2 budget, 30 requests a minute); older versions destroyed"
fi
unset LITELLM_MASTER_KEY
kill $pf 2>/dev/null; trap - EXIT

# 3. The agent token: only its hash goes into the cluster.
tokens=$(kubectl get configmap agent-tokens -n action-gateway -o jsonpath='{.data.tokens\.json}' 2>/dev/null || true)
if [ -z "$tokens" ] || [ "$tokens" = "{}" ] || [ "${NEW_AGENT_TOKEN:-}" = 1 ]; then
  token=$(openssl rand -hex 32)
  hash=$(printf '%s' "$token" | openssl dgst -sha256 | sed 's/^.*= //')
  tokens=$(jq -cn --arg h "$hash" --arg t "$team" '{($h): $t}')
  new_token=$token
  unset token
else
  echo "agent tokens: keeping this session's existing ones (NEW_AGENT_TOKEN=1 makes a new one and drops them)"
fi

# 4. The chart.
helm upgrade --install action-gateway "$here/helm/action-gateway" -n action-gateway \
  --set-string project="$project" --set-string labels.owner="$owner" --set-string labels.session="$session" \
  --set-string image.ref="us-central1-docker.pkg.dev/$project/images/action-gateway@$digest" \
  --set-string audit.bucket="$project-audit" --set-json agentTokens="$tokens" \
  --set-file 'policies.gateway\.rego'="$here/policies/gateway.rego" --set-file 'policies.data\.json'="$here/policies/data.json" \
  "${slack[@]}" --wait --timeout 5m || {
  kubectl get pods,events -n action-gateway --sort-by=.metadata.creationTimestamp | tail -30
  exit 1
}
if [ -n "${new_token:-}" ]; then
  [ -z "${GITHUB_ACTIONS:-}" ] || echo "::add-mask::$new_token"
  echo "agent token for team $team (shown once; only its SHA-256 hash is stored): $new_token"
  echo "connect: kubectl -n action-gateway port-forward deploy/action-gateway 8080:8080, then http://127.0.0.1:8080/mcp with Authorization: Bearer <token> (slack.md, section 5)"
fi
