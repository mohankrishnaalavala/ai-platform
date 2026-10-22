# opa test: at least one test per row of the policy table at the top of gateway.rego.
package gateway_test

import data.gateway

# Wednesday 2026-11-04, 10:00 in Chicago: no freeze. Saturday is inside the weekend freeze.
weekday := "2026-11-04T10:00:00-06:00"

saturday := "2026-11-07T11:00:00-06:00"

clean_dry_run := {"ok": true, "error": "", "pdb_violations": [], "pods_affected": 1}

call(tool, env, args) := {
	"tool": tool,
	"args": args,
	"namespace": {"name": env, "env": env},
	"time": weekday,
	"team": "payments",
	"current": {"replicas": 1},
	"dry_run": clean_dry_run,
}

scale(env, current, replicas) := object.union(
	call("scale_deployment", env, {"namespace": env, "name": "checkout", "replicas": replicas}),
	{"current": {"replicas": current}},
)

apply(env, manifest) := call("apply_manifest", env, {"namespace": env, "manifest": manifest})

# An apply_manifest of a Deployment that has `current` replicas now (0: it doesn't exist yet).
apply_over(env, current, manifest) := object.union(apply(env, manifest), {"current": {"replicas": current}})

container(security_context) := {"name": "web", "image": "x", "securityContext": security_context}

at(facts, t) := object.union(facts, {"time": t})

# Replaces one top-level fact (object.union would merge nested objects).
put(facts, key, value) := object.union(object.remove(facts, [key]), {key: value})

deployment(spec) := {
	"apiVersion": "apps/v1",
	"kind": "Deployment",
	"metadata": {"name": "web"},
	"spec": object.union(
		{
			"replicas": 1,
			"selector": {"matchLabels": {"app": "web"}},
			"template": {
				"metadata": {"labels": {"app": "web"}},
				"spec": {"containers": [{"name": "web", "image": "registry.k8s.io/pause:3.10"}]},
			},
		},
		spec,
	),
}

pod_spec(extra) := {"template": {
	"metadata": {"labels": {"app": "web"}},
	"spec": object.union({"containers": [{"name": "web", "image": "registry.k8s.io/pause:3.10"}]}, extra),
}}

configmap := {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "checkout-config"}, "data": {"LOG_LEVEL": "debug"}}

decide(facts) := r if {
	r := gateway.result with input as facts
}

# The decision is d, and the reasons contain keyword (any case).
expect(facts, d, keyword) if {
	r := decide(facts)
	r.decision == d
	contains(lower(concat(" | ", r.reasons)), lower(keyword))
}

# ---------------------------------------------------------------- row 1: read-only -> allow

test_row1_read_only_allowed_in_every_env if {
	every env in ["dev", "staging", "prod"] {
		expect(call("get_status", env, {"namespace": env, "name": "checkout"}), "allow", "read-only")
	}
}

test_row1_read_only_allowed_during_a_freeze if {
	expect(at(call("get_status", "prod", {"namespace": "prod"}), saturday), "allow", "read-only")
}

# ---------------------------------------------------------------- row 2: dev/staging scale <= 100% -> allow

test_row2_dev_and_staging_scale_up_to_100_percent_allowed if {
	expect(scale("dev", 2, 4), "allow", "dev write") # +100%, the edge
	expect(scale("staging", 2, 3), "allow", "staging write")
	expect(scale("staging", 4, 1), "allow", "staging write") # -75%
	expect(scale("dev", 0, 1), "allow", "dev write") # from 0, measured against 1 replica
}

test_row2_over_100_percent_needs_approval if {
	expect(scale("dev", 2, 5), "needs_approval", "150%")
	expect(scale("staging", 0, 2), "needs_approval", "200%")
}

test_row2_deployment_manifest_over_100_percent_needs_approval if {
	expect(apply_over("staging", 2, deployment({"replicas": 5})), "needs_approval", "150%")
	expect(apply_over("dev", 2, deployment({"replicas": 4})), "allow", "dev write") # +100%, the edge
	expect(apply_over("dev", 0, deployment({})), "allow", "dev write") # a new Deployment with 1 replica
	expect(apply_over("dev", 0, json.remove(deployment({}), ["spec/replicas"])), "allow", "dev write") # defaults to 1
	expect(apply_over("dev", 0, deployment({"replicas": 3})), "needs_approval", "300%")
	expect(apply_over("dev", 2, deployment({"replicas": -1})), "deny", "whole number")
	expect(object.remove(apply("dev", deployment({})), ["current"]), "deny", "current replica count")
}

test_row2_other_dev_and_staging_writes_allowed if {
	expect(call("restart_deployment", "staging", {"namespace": "staging", "name": "checkout"}), "allow", "staging write")
	expect(call("rollout_undo", "dev", {"namespace": "dev", "name": "checkout"}), "allow", "dev write")
	expect(apply("dev", configmap), "allow", "dev write")
	expect(apply("staging", deployment({})), "allow", "staging write")
	expect(call("undo_action", "dev", {"action_id": "a-1"}), "allow", "dev write")
}

# ---------------------------------------------------------------- row 3: prod write -> needs_approval

test_row3_every_prod_write_needs_approval if {
	expect(scale("prod", 2, 3), "needs_approval", "production")
	expect(call("restart_deployment", "prod", {"namespace": "prod", "name": "checkout"}), "needs_approval", "production")
	expect(call("rollout_undo", "prod", {"namespace": "prod", "name": "checkout", "to_revision": 1}), "needs_approval", "production")
	expect(apply("prod", configmap), "needs_approval", "production")
	expect(call("undo_action", "prod", {"action_id": "a-1"}), "needs_approval", "production")
	decide(scale("prod", 2, 3)).approver_team == "" # any approver
}

# ---------------------------------------------------------------- row 4: scale to 0 in prod -> deny

test_row4_scale_to_zero_in_prod_denied if {
	expect(scale("prod", 2, 0), "deny", "0 replicas in prod")
}

test_row4_manifest_with_zero_replicas_in_prod_denied if {
	expect(apply("prod", deployment({"replicas": 0})), "deny", "0 replicas in prod")
}

test_row4_break_glass_cannot_scale_prod_to_zero if {
	expect(object.union(at(scale("prod", 2, 0), saturday), {"team": "break-glass"}), "deny", "0 replicas in prod")
}

test_row4_scale_to_zero_outside_prod_allowed if {
	expect(scale("dev", 2, 0), "allow", "dev write")
}

# ---------------------------------------------------------------- row 5: freeze in prod -> deny unless break-glass

test_row5_freeze_denies_prod_writes if {
	expect(at(scale("prod", 2, 3), saturday), "deny", "weekend freeze")
	expect(at(call("restart_deployment", "prod", {"namespace": "prod", "name": "checkout"}), saturday), "deny", "freeze")
}

test_row5_freeze_edges if {
	expect(at(scale("prod", 2, 3), "2026-11-06T16:59:00-06:00"), "needs_approval", "production") # Friday 16:59
	expect(at(scale("prod", 2, 3), "2026-11-06T17:00:00-06:00"), "deny", "freeze") # Friday 17:00
	expect(at(scale("prod", 2, 3), "2026-11-09T07:59:00-06:00"), "deny", "freeze") # Monday 07:59
	expect(at(scale("prod", 2, 3), "2026-11-09T08:00:00-06:00"), "needs_approval", "production") # Monday 08:00
}

# The same UTC clock time on two Fridays: 16:30 in Chicago after daylight saving ends (Nov 1), 17:30 before.
test_row5_freeze_uses_its_named_time_zone if {
	expect(at(scale("prod", 2, 3), "2026-11-06T22:30:00Z"), "needs_approval", "production")
	expect(at(scale("prod", 2, 3), "2026-10-30T22:30:00Z"), "deny", "freeze")
}

test_row5_freeze_applies_only_to_prod if {
	expect(at(scale("staging", 2, 3), saturday), "allow", "staging write")
}

test_row5_break_glass_team_needs_a_break_glass_approver if {
	r := decide(object.union(at(scale("prod", 2, 3), saturday), {"team": "break-glass"}))
	r.decision == "needs_approval"
	r.approver_team == "break-glass"
	contains(concat(" ", r.reasons), "break-glass")
}

test_row5_invalid_freeze_data_fails_closed if {
	bad := [{"name": "typo", "envs": ["prod"], "time_zone": "America/Chicgo", "start": "Friday 17:00", "end": "Monday 08:00"}]
	r := decide(scale("prod", 2, 3)) with data.gateway_config.freezes as bad
	r.decision == "deny"
	contains(concat(" ", r.reasons), "invalid")
}

# ---------------------------------------------------------------- row 6: host access and privilege -> deny

test_row6_privileged_container_denied if {
	m := deployment(pod_spec({"containers": [{"name": "web", "image": "x", "securityContext": {"privileged": true}}]}))
	expect(apply("dev", m), "deny", "privileged")
}

test_row6_privileged_init_container_denied if {
	m := deployment(pod_spec({"initContainers": [{"name": "init", "image": "x", "securityContext": {"privileged": true}}]}))
	expect(apply("staging", m), "deny", "privileged")
}

test_row6_host_network_denied if {
	expect(apply("dev", deployment(pod_spec({"hostNetwork": true}))), "deny", "hostNetwork")
}

test_row6_host_path_denied if {
	m := deployment(pod_spec({"volumes": [{"name": "root", "hostPath": {"path": "/"}}]}))
	expect(apply("dev", m), "deny", "hostPath")
}

test_row6_host_pid_and_host_ipc_denied if {
	expect(apply("dev", deployment(pod_spec({"hostPID": true}))), "deny", "hostPID: true")
	expect(apply("staging", deployment(pod_spec({"hostIPC": true}))), "deny", "hostIPC: true")
}

test_row6_added_capabilities_denied if {
	expect(apply("dev", deployment(pod_spec({"containers": [container({"capabilities": {"add": ["NET_ADMIN"]}})]}))), "deny", "adds Linux capabilities")
	expect(apply("dev", deployment(pod_spec({"initContainers": [container({"capabilities": {"add": ["SYS_ADMIN"]}})]}))), "deny", "SYS_ADMIN")
	expect(apply("dev", deployment(pod_spec({"containers": [container({"capabilities": {"add": "SYS_ADMIN"}})]}))), "deny", "adds Linux capabilities") # malformed
}

test_row6_privilege_escalation_denied if {
	expect(apply("dev", deployment(pod_spec({"containers": [container({"allowPrivilegeEscalation": true})]}))), "deny", "allowPrivilegeEscalation: true")
	expect(apply("dev", deployment(pod_spec({"initContainers": [container({"allowPrivilegeEscalation": true})]}))), "deny", "allowPrivilegeEscalation: true")
}

test_row6_safe_settings_allowed if {
	safe := {"privileged": false, "allowPrivilegeEscalation": false, "capabilities": {"add": [], "drop": ["ALL"]}}
	expect(apply("dev", deployment(pod_spec({"hostPID": false, "containers": [container(safe)]}))), "allow", "dev write")
}

# ---------------------------------------------------------------- row 7: failed dry run or PDB violation -> deny

test_row7_failed_dry_run_denied if {
	f := object.union(apply("dev", deployment({})), {"dry_run": {"ok": false, "error": "selector does not match template labels", "pdb_violations": [], "pods_affected": 0}})
	expect(f, "deny", "selector does not match")
}

test_row7_missing_dry_run_denied if {
	expect(object.remove(scale("dev", 2, 3), ["dry_run"]), "deny", "dry run failed or incomplete")
	expect(put(scale("dev", 2, 3), "dry_run", {"ok": true}), "deny", "dry run failed or incomplete")
}

test_row7_pdb_violation_denied if {
	f := object.union(call("restart_deployment", "staging", {"namespace": "staging", "name": "payments"}), {"dry_run": {"ok": true, "error": "", "pdb_violations": ["payments"], "pods_affected": 1}})
	expect(f, "deny", "PodDisruptionBudget payments")
}

# ---------------------------------------------------------------- row 8: unknown argument or no env label -> deny

test_row8_unknown_argument_denied if {
	f := scale("prod", 2, 3)
	expect(object.union(f, {"args": object.union(f.args, {"approved_by": "break-glass"})}), "deny", "unknown argument \"approved_by\"")
	expect(call("get_status", "dev", {"namespace": "dev", "verbose": true}), "deny", "unknown argument")
}

test_row8_missing_env_label_denied if {
	expect(put(scale("sandbox", 2, 3), "namespace", {"name": "sandbox"}), "deny", "no env label")
	expect(put(call("get_status", "sandbox", {"namespace": "sandbox"}), "namespace", {"name": "sandbox"}), "deny", "no env label")
}

test_row8_unknown_env_value_denied if {
	expect(scale("qa", 2, 3), "deny", "doesn't know")
}

test_row8_unknown_tool_denied if {
	expect(call("delete_namespace", "dev", {"namespace": "dev"}), "deny", "unknown tool delete_namespace")
}

test_row8_missing_or_malformed_arguments_denied if {
	expect(call("restart_deployment", "dev", {"namespace": "dev"}), "deny", "missing argument \"name\"")
	expect(call("restart_deployment", "dev", "dev/checkout"), "deny", "not an object")
	expect(scale("dev", 2, -1), "deny", "whole number")
	expect(scale("dev", 2, "3"), "deny", "whole number")
	expect(scale("dev", 2, 1.5), "deny", "whole number")
	expect(object.remove(scale("dev", 2, 3), ["current"]), "deny", "current replica count")
	expect(scale("dev", -1, 3), "deny", "current replica count")
	expect(scale("dev", "2", 3), "deny", "current replica count")
	expect(scale("dev", 1.5, 3), "deny", "current replica count")
}

test_row8_missing_time_or_team_denied if {
	expect(object.remove(scale("dev", 2, 3), ["time"]), "deny", "server time")
	expect(at(scale("dev", 2, 3), "Saturday 11:00"), "deny", "server time")
	expect(object.remove(scale("dev", 2, 3), ["team"]), "deny", "requester team")
	expect(object.union(scale("dev", 2, 3), {"team": ""}), "deny", "requester team")
}

# ---------------------------------------------------------------- also: blast radius, kinds, namespace, irreversible

test_blast_radius_over_the_limit_denied if {
	big := {"ok": true, "error": "", "pdb_violations": [], "pods_affected": 21}
	expect(object.union(scale("staging", 2, 23), {"dry_run": big}), "deny", "blast radius")
	expect(object.union(scale("dev", 2, 22), {"dry_run": object.union(big, {"pods_affected": 20})}), "needs_approval", "1000%")
}

test_only_configmap_and_deployment_kinds if {
	expect(apply("dev", {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "x"}}), "deny", "kind ClusterRoleBinding")
	expect(apply("dev", [configmap, configmap]), "deny", "not one Kubernetes object")
}

test_looked_up_namespace_must_be_the_called_one if {
	expect(put(scale("dev", 2, 3), "namespace", {"name": "prod", "env": "prod"}), "deny", "looked up namespace prod, but the call names dev")
	expect(put(scale("dev", 2, 3), "namespace", {"env": "dev"}), "deny", "looked up namespace")
	expect(call("undo_action", "dev", {"action_id": "a-1"}), "allow", "dev write") # no namespace argument to compare
}

test_manifest_must_stay_in_the_called_namespace if {
	m := object.union(configmap, {"metadata": {"name": "checkout-config", "namespace": "prod"}})
	expect(apply("dev", m), "deny", "names namespace prod")
	expect(apply("dev", object.union(configmap, {"metadata": {"name": "c", "namespace": "dev"}})), "allow", "dev write")
}

test_irreversible_always_needs_approval if {
	r := decide(apply("dev", object.union(configmap, {"immutable": true})))
	r.decision == "needs_approval"
	r.irreversible == true
	contains(concat(" ", r.reasons), "irreversible")
	expect(call("restart_deployment", "staging", {"namespace": "staging", "name": "checkout"}), "allow", "staging write")
	expect(call("restart_deployment", "staging", {"namespace": "staging", "name": "checkout"}), "needs_approval", "marked irreversible") with data.gateway_config.tools.restart_deployment.irreversible as true
}

test_result_shape if {
	r := decide(object.union(object.remove(scale("prod", 2, 0), ["team"]), {"args": {"namespace": "prod", "name": "checkout", "replicas": 0, "x": 1}}))
	r.decision == "deny"
	r.reasons == sort(r.reasons)
	count(r.reasons) == 3 # scale to 0, unknown argument, no team
	r.policy_version == "1"
	r.irreversible == false
}
