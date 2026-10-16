#!/usr/bin/env bash
# Stage 0 (P0-02): get the project ready for Terraform to log in without keys.
# Run once by a project owner. Safe to run again: every step checks before it creates,
# and a second run only prints "skipped". Works with the bash 3.2 that ships with macOS.
#
# Inputs: infra/bootstrap/.env (gitignored) or the environment. See .env.example.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
[ -f "$here/.env" ] && . "$here/.env"

: "${PROJECT_ID:?set PROJECT_ID}" "${BILLING_ACCOUNT_ID:?set BILLING_ACCOUNT_ID}"
: "${GITHUB_REPO:?set GITHUB_REPO (owner/repo)}" "${USER_EMAIL:?set USER_EMAIL}" "${OWNER:?set OWNER (label value)}"
REGION=${REGION:-us-central1}

# Only the ai-platform configuration, never the machine default; never wait on a prompt.
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1

log() { printf '%s\n' "$*"; }
die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }

# True if the IAM policy (JSON) has an unconditional binding of role to member.
has_binding() {
  jq -e --arg r "$2" --arg m "$3" \
    'any(.bindings[]?; .role == $r and .condition == null and any(.members[]; . == $m))' <<<"$1" >/dev/null
}

# --- 1. Guards: stop before any change if this isn't the right project and account.
for tool in gcloud gh jq; do command -v "$tool" >/dev/null || die "$tool is not installed"; done
[ "$(gcloud config get project 2>/dev/null)" = "$PROJECT_ID" ] \
  || die "the ai-platform gcloud configuration does not point at $PROJECT_ID"
[ "$(gcloud config get account 2>/dev/null)" = "$USER_EMAIL" ] \
  || die "the ai-platform gcloud configuration is not logged in as $USER_EMAIL"
[ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)')" = "billingAccounts/$BILLING_ACCOUNT_ID" ] \
  || die "$PROJECT_ID is not linked to billing account $BILLING_ACCOUNT_ID"

project_number=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
repo_id=$(gh api "repos/$GITHUB_REPO" --jq .id)
repo_name=$(gh api "repos/$GITHUB_REPO" --jq .full_name)   # exact case, as GitHub puts it in tokens
log "project $PROJECT_ID, repo $repo_name (id $repo_id)"

# --- 2. APIs
apis="iam iamcredentials sts cloudresourcemanager cloudbilling storage compute artifactregistry
billingbudgets container containerfilesystem secretmanager logging monitoring cloudquotas"
enabled=$(gcloud services list --enabled --format='value(config.name)')
missing=()
for a in $apis; do
  grep -qx "$a.googleapis.com" <<<"$enabled" || missing+=("$a.googleapis.com")
done
if [ ${#missing[@]} -gt 0 ]; then
  log "enabling ${missing[*]}"
  gcloud services enable "${missing[@]}"
else
  log "APIs: all enabled, skipped"
fi

# --- 3. Default network: its firewall rules allow SSH and RDP from anywhere. The foundation builds its own VPC.
if [[ " ${missing[*]-} " == *" compute.googleapis.com "* ]]; then
  # Compute Engine creates the default network shortly after it is enabled; wait for it, or we'd miss it.
  for _ in $(seq 18); do gcloud compute networks describe default >/dev/null 2>&1 && break; sleep 5; done
fi
if gcloud compute networks describe default >/dev/null 2>&1; then
  rules=()
  while IFS= read -r r; do [ -n "$r" ] && rules+=("$r"); done \
    < <(gcloud compute firewall-rules list --filter='network~/networks/default$' --format='value(name)')
  if [ ${#rules[@]} -gt 0 ]; then
    log "deleting firewall rules on default: ${rules[*]}"
    gcloud compute firewall-rules delete "${rules[@]}"
  fi
  log "deleting the default network"
  gcloud compute networks delete default
else
  log "default network: absent, skipped"
fi

# --- 4. Terraform state bucket: versioned, never public, in one region (inside the free tier).
bucket="gs://${PROJECT_ID}-tfstate"
want="true;true;enforced;${REGION};ai-platform;00;${OWNER}"
bucket_settings() {
  gcloud storage buckets describe "$bucket" --format=json | jq -r '[.versioning_enabled, .uniform_bucket_level_access,
    .public_access_prevention, .location, .labels.app, .labels.layer, .labels.owner] | join(";")' \
    | tr '[:upper:]' '[:lower:]'
}
if gcloud storage buckets describe "$bucket" >/dev/null 2>&1; then
  log "state bucket: exists, skipped"
else
  log "creating $bucket"
  gcloud storage buckets create "$bucket" --location="$REGION" --uniform-bucket-level-access \
    --public-access-prevention --default-storage-class=STANDARD
  gcloud storage buckets update "$bucket" --versioning --update-labels="app=ai-platform,layer=00,owner=$OWNER"
fi
got=$(bucket_settings)
want=$(tr '[:upper:]' '[:lower:]' <<<"$want")
[ "$got" = "$want" ] || die "$bucket settings are '$got', expected '$want' (versioning;uniform access;public access prevention;location;labels app,layer,owner). Fix by hand."

# --- 5. Terraform service account: admin roles only for what infra/foundation manages. Not Owner.
sa="terraform@${PROJECT_ID}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "$sa" >/dev/null 2>&1; then
  log "terraform service account: exists, skipped"
else
  log "creating $sa"
  gcloud iam service-accounts create terraform --display-name="Terraform (foundation)" \
    --description="Applies infra/foundation. No keys: foundation.yml uses it through WIF, the owner by impersonation."
  # A new service account can take a minute to become visible to IAM.
  for _ in $(seq 24); do gcloud iam service-accounts describe "$sa" >/dev/null 2>&1 && break; sleep 5; done
fi

policy=$(gcloud projects get-iam-policy "$PROJECT_ID" --format=json)
for role in roles/resourcemanager.projectIamAdmin roles/iam.serviceAccountAdmin roles/compute.networkAdmin \
            roles/storage.admin roles/artifactregistry.admin; do
  if has_binding "$policy" "$role" "serviceAccount:$sa"; then
    log "project $role: granted, skipped"
  else
    log "granting $role on the project"
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$sa" --role="$role" \
      --condition=None >/dev/null
  fi
done

billing_policy=$(gcloud billing accounts get-iam-policy "$BILLING_ACCOUNT_ID" --format=json)
if has_binding "$billing_policy" roles/billing.costsManager "serviceAccount:$sa"; then
  log "billing roles/billing.costsManager: granted, skipped"
else
  log "granting roles/billing.costsManager on the billing account (budgets)"
  gcloud billing accounts add-iam-policy-binding "$BILLING_ACCOUNT_ID" --member="serviceAccount:$sa" \
    --role=roles/billing.costsManager >/dev/null
fi

# --- 6. Keyless login for GitHub: only this repo (by ID; names can be re-registered) on main.
pool=github provider=github-actions
mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref,attribute.workflow=assertion.workflow_ref.split('@')[0]"
condition="assertion.repository_id=='$repo_id' && assertion.ref=='refs/heads/main'"

case "$(gcloud iam workload-identity-pools describe "$pool" --location=global --format='value(state)' 2>/dev/null || true)" in
  ACTIVE)  log "WIF pool: exists, skipped" ;;
  DELETED) log "undeleting WIF pool (deleted pools keep their name for 30 days)"
           gcloud iam workload-identity-pools undelete "$pool" --location=global ;;
  *)       log "creating WIF pool $pool"
           gcloud iam workload-identity-pools create "$pool" --location=global --display-name="GitHub Actions" ;;
esac

describe_provider() {
  gcloud iam workload-identity-pools providers describe "$provider" --location=global \
    --workload-identity-pool="$pool" --format=json
}
case "$(describe_provider 2>/dev/null | jq -r '.state // empty' || true)" in
  ACTIVE)  log "WIF provider: exists, skipped" ;;
  DELETED) log "undeleting WIF provider"
           gcloud iam workload-identity-pools providers undelete "$provider" --location=global \
             --workload-identity-pool="$pool" ;;
  *)       log "creating WIF provider $provider"
           gcloud iam workload-identity-pools providers create-oidc "$provider" --location=global \
             --workload-identity-pool="$pool" --display-name="GitHub Actions" \
             --issuer-uri=https://token.actions.githubusercontent.com \
             --attribute-mapping="$mapping" --attribute-condition="$condition" ;;
esac
provider_json=$(describe_provider)
jq -e --arg c "$condition" --arg wf "assertion.workflow_ref.split('@')[0]" '
  .attributeCondition == $c and .oidc.issuerUri == "https://token.actions.githubusercontent.com" and
  .attributeMapping == {"google.subject": "assertion.sub", "attribute.repository_id": "assertion.repository_id",
                        "attribute.ref": "assertion.ref", "attribute.workflow": $wf}' <<<"$provider_json" >/dev/null \
  || die "WIF provider settings differ from what bootstrap creates. Fix by hand; bootstrap won't change them silently."
# Another provider in the pool could map attribute.workflow differently and reach the same binding.
others=$(gcloud iam workload-identity-pools providers list --location=global --workload-identity-pool="$pool" \
  --format='value(name.basename())' | grep -vx "$provider" || true)
[ -z "$others" ] || die "WIF pool $pool has other providers ($others). Remove them by hand."

# Only foundation.yml (on main, in this repo) may act as the terraform service account.
wif_member="principalSet://iam.googleapis.com/projects/$project_number/locations/global/workloadIdentityPools/$pool/attribute.workflow/$repo_name/.github/workflows/foundation.yml"
sa_policy=$(gcloud iam service-accounts get-iam-policy "$sa" --format=json)
if has_binding "$sa_policy" roles/iam.workloadIdentityUser "$wif_member"; then
  log "foundation.yml may act as terraform: granted, skipped"
else
  log "letting foundation.yml act as the terraform service account"
  gcloud iam service-accounts add-iam-policy-binding "$sa" --role=roles/iam.workloadIdentityUser \
    --member="$wif_member" >/dev/null
fi

# --- 7. The owner impersonates the terraform service account from the laptop: a short-lived token, no key.
if has_binding "$sa_policy" roles/iam.serviceAccountTokenCreator "user:$USER_EMAIL"; then
  log "Token Creator for $USER_EMAIL: granted, skipped"
else
  log "granting Token Creator on the terraform service account to $USER_EMAIL"
  gcloud iam service-accounts add-iam-policy-binding "$sa" --role=roles/iam.serviceAccountTokenCreator \
    --member="user:$USER_EMAIL" >/dev/null
fi

# --- 8. Custom role for the session's Cloud Router and NAT (P1). Created here by the owner, so the
# terraform service account needs no role-admin rights; the foundation grants it to the deployer.
nat_perms="compute.networks.updatePolicy,compute.routers.create,compute.routers.delete,compute.routers.get,compute.routers.update"
role_json=$(gcloud iam roles describe sessionNat --project="$PROJECT_ID" --format=json 2>/dev/null || true)
if [ -z "$role_json" ]; then
  log "creating custom role sessionNat"
  gcloud iam roles create sessionNat --project="$PROJECT_ID" --title="Session Cloud NAT" \
    --description="Create and delete the session's Cloud Router and NAT. Granted to the deployer." \
    --permissions="$nat_perms" --stage=GA >/dev/null
  role_json=$(gcloud iam roles describe sessionNat --project="$PROJECT_ID" --format=json)
elif jq -e '.deleted == true' <<<"$role_json" >/dev/null; then
  log "undeleting custom role sessionNat (deleted roles keep their name for a while)"
  gcloud iam roles undelete sessionNat --project="$PROJECT_ID" >/dev/null
  role_json=$(gcloud iam roles describe sessionNat --project="$PROJECT_ID" --format=json)
else
  log "custom role sessionNat: exists, skipped"
fi
jq -e --arg p "$nat_perms" '(.includedPermissions | sort | join(",")) == $p' <<<"$role_json" >/dev/null \
  || die "custom role sessionNat has other permissions than $nat_perms. Fix by hand; bootstrap won't change them silently."

log ""
log "Done. Next steps need these:"
log "  terraform service account: $sa"
log "  WIF provider: projects/$project_number/locations/global/workloadIdentityPools/$pool/providers/$provider"
log "  state bucket: $bucket"
