# kind smoke test only: a stand-in used while policies/ (P5-03, PR #70) isn't in the repo; kind-smoke.sh
# loads policies/ instead once it is. It is NOT the v1 policy table. Same input and answer shape as
# policies/gateway.rego: reads allow, a failed dry run denies, prod writes need approval, dev and staging
# writes allow, anything else denies.
package gateway

answer(decision, reason) := {"decision": decision, "reasons": [reason], "policy_version": "stand-in",
	"approver_team": "", "irreversible": false}

result := answer("allow", "read-only call") if {
	input.tool == "get_status"
} else := answer("deny", "dry run failed") if {
	not input.dry_run.ok
} else := answer("needs_approval", "prod write") if {
	input.namespace.env == "prod"
} else := answer("allow", "dev or staging write") if {
	input.namespace.env in {"dev", "staging"}
} else := answer("deny", "namespace has no env label")
