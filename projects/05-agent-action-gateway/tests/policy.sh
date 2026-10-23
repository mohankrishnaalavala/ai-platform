#!/usr/bin/env bash
# The policy, no cluster needed (CI runs this on every pull request):
# - opa test: the Rego unit tests, at least one per row of the policy table in policies/gateway.rego
# - every scenario's expected decision and reason keywords against the Rego (tests/check_scenarios.py)
set -euo pipefail
cd "$(dirname "$0")/.."

export OPA_IMAGE=openpolicyagent/opa:1.21.1@sha256:d3a9a6bd4fbcb0f4403294b0d0b239b07be163ac118a46df7d50045b52e52053
docker run --rm -v "$PWD/policies:/policies:ro" "$OPA_IMAGE" test /policies -v
python3 tests/check_scenarios.py
