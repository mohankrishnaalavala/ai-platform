# Every command the READMEs use. Cloud targets act as a service account through a one-hour token
# from the ai-platform gcloud configuration: no keys, no machine-wide credentials (see infra/README.md).
# Works with the GNU Make 3.81 that ships with macOS. Recipes fail closed: when a check can't
# run, the target fails instead of reporting "clean".

SHELL := /bin/bash
export CLOUDSDK_ACTIVE_CONFIG_NAME := ai-platform
export CLOUDSDK_CORE_DISABLE_PROMPTS := 1
# One kubeconfig for this platform (kind and GKE contexts); ~/.kube/config stays untouched.
export KUBECONFIG := $(HOME)/.kube/ai-platform
unexport GOOGLE_APPLICATION_CREDENTIALS GOOGLE_CREDENTIALS GOOGLE_BACKEND_CREDENTIALS \
	GOOGLE_IMPERSONATE_SERVICE_ACCOUNT GOOGLE_BACKEND_IMPERSONATE_SERVICE_ACCOUNT \
	GOOGLE_CLOUD_KEYFILE_JSON GCLOUD_KEYFILE_JSON CLOUDSDK_CORE_PROJECT

ZONE ?= us-central1-c
LAYERS ?=
# Cluster autoscaler profile (BALANCED or OPTIMIZE_UTILIZATION); empty keeps Terraform's default.
PROFILE ?=
# Spot GPU nodes (true) or on-demand (false); empty keeps Terraform's default (spot).
SPOT ?=
P ?= 00
SESSION := $(shell date -u +%Y%m%dt%H%Mz)

# Looked up once, and only when a target uses them, so `make help` needs no cloud access.
# $(shell) doesn't see the exports above, so it names the configuration itself.
gcloud_shell = CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1 gcloud
PROJECT = $(eval PROJECT := $(shell $(gcloud_shell) config get project 2>/dev/null))$(PROJECT)
OWNER = $(eval OWNER := $(shell $(gcloud_shell) projects describe $(PROJECT) --format='value(labels.owner)'))$(OWNER)

# Terraform as a service account: $(call tf,deployer,session) apply ...
# An empty token stops the line; otherwise Terraform would fall back to machine-wide credentials.
# In GitHub Actions the WIF login already is that service account, so it isn't impersonated again.
as = export GOOGLE_OAUTH_ACCESS_TOKEN="$$(gcloud auth print-access-token $(if $(GITHUB_ACTIONS),,--impersonate-service-account=$(1)@$(PROJECT).iam.gserviceaccount.com))" && [ -n "$$GOOGLE_OAUTH_ACCESS_TOKEN" ] &&
tf = $(call as,$(1)) terraform -chdir=infra/$(2) init -input=false -reconfigure -backend-config="bucket=$(PROJECT)-tfstate" >/dev/null && terraform -chdir=infra/$(2)
session_vars = -var="project_id=$(PROJECT)" -var="owner=$(OWNER)" -var="session=$(SESSION)" -var="zone=$(ZONE)" \
	$(if $(PROFILE),-var="autoscaling_profile=$(PROFILE)") $(if $(SPOT),-var="gpu_spot=$(SPOT)")

.PHONY: help check-project up deploy down status test bench team evals preflight park foundation-plan foundation-apply

help:
	@echo "make up | down | status        session cluster (cloud, costs money while up)"
	@echo "make up LAYERS=01               also deploy those layers (each project's deploy.sh)"
	@echo "make deploy LAYERS=\"04 05\"     deploy only those layers again, on the running cluster"
	@echo "make test P=00                 run one project's tests"
	@echo "make bench [CONFIG=… SCENARIOS=… CONCURRENCY=…]  P1 benchmarks, from a Job in the session cluster"
	@echo "make team NAME=… BUDGET_USD=… RPM=… [TPM=…]  P2: onboard a team on the gateway; prints its key once"
	@echo "make evals TARGET=… CONFIG=… [REPEAT=1 RUNS=2]  P4: the golden set against a gateway alias; results to commit"
	@echo "make preflight                 read-only checks before make up for the P4 + P5 window (OK or MISSING)"
	@echo "make foundation-plan | -apply  the always-on foundation, as the terraform service account"
	@echo "make park CONFIRM=yes          empty Artifact Registry and the buckets (keeps Terraform state)"

# Stop unless gcloud points at this platform's project (it carries the label app=ai-platform).
check-project:
	@[ -n "$(PROJECT)" ] && [ "$$(gcloud projects describe $(PROJECT) --format='value(labels.app)')" = ai-platform ] \
		|| { echo "the ai-platform gcloud configuration does not point at the platform project"; exit 1; }

# Each layer's deploy.sh, in order. They use helm upgrade --install, so running one again is safe.
deploy_layers = for l in $(LAYERS); do d=$$(ls -d projects/$$l-*/ 2>/dev/null | head -1); \
		[ -f "$${d}deploy.sh" ] || { echo "no deploy.sh for layer $$l"; exit 1; }; \
		ZONE=$(ZONE) bash "$${d}deploy.sh" || exit 1; \
	done

up: check-project
	$(call tf,deployer,session) apply -input=false -auto-approve $(session_vars)
	gcloud container clusters get-credentials ai-platform --location=$(ZONE) --dns-endpoint
	kubectl get nodes
	@$(deploy_layers)

# A fix on a cluster that stays up: only the named layers, no Terraform.
deploy: check-project
	@[ -n "$(LAYERS)" ] || { echo 'usage: make deploy LAYERS="04 05"'; exit 1; }
	gcloud container clusters get-credentials ai-platform --location=$(ZONE) --dns-endpoint
	@$(deploy_layers)

# Helm releases and PVCs go first: a PVC left behind becomes a persistent disk that keeps billing.
# Releases go newest first, so vLLM's ScaledObject is removed while KEDA can still clear its finalizer.
# Only a confirmed "not found" (404) skips that step; any other describe error stops `down`.
down: check-project
	@set -o pipefail; \
	if err=$$(gcloud container clusters describe ai-platform --location=$(ZONE) 2>&1 >/dev/null); then \
		gcloud container clusters get-credentials ai-platform --location=$(ZONE) --dns-endpoint || exit 1; \
		releases=$$(helm list -A -o json | jq -r 'sort_by(.updated) | reverse | .[] | "\(.name) \(.namespace)"') || exit 1; \
		while read -r name ns; do \
			[ -z "$$name" ] || helm uninstall "$$name" -n "$$ns" --wait || exit 1; \
		done <<< "$$releases"; \
		kubectl delete pvc --all --all-namespaces --wait=true --timeout=10m || exit 1; \
	elif grep -qE "code=404|NOT_FOUND" <<< "$$err"; then \
		echo "no cluster: nothing to uninstall"; \
	else \
		echo "$$err"; echo "can't tell whether the cluster exists; stopping before destroy"; exit 1; \
	fi
	@$(call tf,deployer,session) destroy -input=false -auto-approve $(session_vars); rc=$$?; \
		$(MAKE) --no-print-directory status; exit $$rc

# The project holds only this platform, so list everything, not just labeled resources:
# an unlabeled leftover still bills. Exits 1 if anything billable remains or a list fails.
status: check-project
	@n=0; \
	for kind in "container clusters" "compute instances" "compute disks" "compute forwarding-rules" "compute addresses" "compute routers"; do \
		found=$$(gcloud $$kind list --format='value(name)') || found="LIST FAILED"; \
		printf '%-26s %s\n' "$$kind:" "$${found:-none}"; \
		[ -z "$$found" ] || n=$$((n + 1)); \
	done; \
	[ $$n -eq 0 ] && echo "nothing billable is running" || { echo "billable resources remain, or a list failed"; exit 1; }

test:
	@t=$$(ls projects/$(P)-*/tests/run.sh 2>/dev/null | head -1); \
	[ -n "$$t" ] || { echo "no tests for P=$(P) yet"; exit 1; }; \
	bash "$$t"

# P1 benchmarks against the running session (make up LAYERS=01). CONFIG, SCENARIOS and
# CONCURRENCY pass through from the environment; results go to the results bucket.
bench: check-project
	ZONE=$(ZONE) bash projects/01-gpu-serving/bench/bench.sh

# P2: onboard a team on the running gateway (make up LAYERS=02). The master key is read as the deployer
# straight into the script's environment, never printed; the team's key is printed once. NAME and the
# numbers reach the script through the environment ($$NAME), never pasted into the shell line by Make.
team: check-project
	@[ -n "$$NAME" ] && [ -n "$$BUDGET_USD" ] && [ -n "$$RPM" ] || { echo "usage: make team NAME=payments BUDGET_USD=20 RPM=60 [TPM=100000]"; exit 1; }
	@set -e; kubectl port-forward -n gateway svc/gateway 14000:4000 >/dev/null & pf=$$!; trap 'kill $$pf 2>/dev/null' EXIT; \
	up=; for _ in $$(seq 30); do curl -sf http://127.0.0.1:14000/health/liveliness >/dev/null && up=1 && break; sleep 1; done; \
	[ -n "$$up" ] || { echo "the gateway isn't reachable on 127.0.0.1:14000 (make up LAYERS=02? port in use?)"; exit 1; }; \
	master=$$(gcloud secrets versions access latest --secret=litellm-master-key $(if $(GITHUB_ACTIONS),,--impersonate-service-account=deployer@$(PROJECT).iam.gserviceaccount.com) 2>/dev/null) || true; \
	[ -n "$$master" ] || { echo "couldn't read the gateway's master key"; exit 1; }; \
	LITELLM_MASTER_KEY="$$master" GATEWAY_URL=http://127.0.0.1:14000 python3 projects/02-llm-gateway/scripts/onboard_team.py "$$NAME" "$$BUDGET_USD" "$$RPM" $${TPM:+"$$TPM"}

# P4: the golden set against one gateway alias of the running session (make up LAYERS="01 02 03 04"), from the
# laptop. Two runs by default, two results files (the noise band). The master key is read as the deployer
# straight into the script's environment, never printed. TARGET, CONFIG, REPEAT and RUNS reach the script
# through the environment, never pasted into the shell line by Make.
evals: check-project
	@ZONE=$(ZONE) bash projects/04-eval-gated-releases/scripts/evals.sh

# Before `make up` for the P4 + P5 window. Read-only: lists metadata, never reads a secret's value. No
# check-project here, so a wrong project prints as MISSING with every other check, instead of stopping.
preflight:
	@bash scripts/preflight.sh

foundation-plan: check-project
	@email=$$(gcloud config get account) && \
	billing=$$(gcloud billing projects describe $(PROJECT) --format='value(billingAccountName)') && billing=$${billing#billingAccounts/} && \
	repo=$$(gh repo view --json nameWithOwner -q .nameWithOwner) && \
	[ -n "$$email" ] && [ -n "$$billing" ] && [ -n "$$repo" ] || { echo "couldn't read account, billing account or repo"; exit 1; }; \
	$(call tf,terraform,foundation) plan -input=false -out=foundation.tfplan \
		-var="project_id=$(PROJECT)" -var="owner=$(OWNER)" -var="owner_email=$$email" \
		-var="billing_account_id=$$billing" -var="github_repo=$$repo"

foundation-apply: check-project
	$(call tf,terraform,foundation) apply -input=false foundation.tfplan
	rm -f infra/foundation/foundation.tfplan

# Deletes data, so it needs CONFIRM=yes and shows what it will delete. The audit bucket's objects
# can't be deleted until their retention ends. Fails if anything is left afterwards.
repo = us-central1-docker.pkg.dev/$(PROJECT)/images
park: check-project
	@[ "$(CONFIRM)" = yes ] || { echo "park deletes all images and bucket objects; run: make park CONFIRM=yes"; exit 1; }
	@set -o pipefail; left=0; \
	for b in models results audit; do \
		objs=$$(gcloud storage ls --recursive "gs://$(PROJECT)-$$b/**" 2>&1); rc=$$?; \
		if [ $$rc -ne 0 ] && ! grep -q 'One or more URLs matched no objects' <<< "$$objs"; then echo "$$b: list failed"; left=1; continue; fi; \
		if [ $$rc -ne 0 ]; then echo "$$b: already empty"; continue; fi; \
		echo "$$b: deleting $$(wc -l <<< "$$objs" | tr -d ' ') objects"; \
		gcloud storage rm --recursive "gs://$(PROJECT)-$$b/**" || { echo "$$b: some objects are still under retention"; left=1; }; \
	done; \
	images=$$(gcloud artifacts docker images list $(repo) --format='value(package)' | sort -u) || { echo "images: list failed"; exit 1; }; \
	for img in $$images; do gcloud artifacts docker images delete "$$img" --delete-tags || left=1; done; \
	[ -z "$$(gcloud artifacts docker images list $(repo) --format='value(package)')" ] || left=1; \
	[ $$left -eq 0 ] && echo "parked: buckets and images are empty" || { echo "park incomplete (see above)"; exit 1; }
