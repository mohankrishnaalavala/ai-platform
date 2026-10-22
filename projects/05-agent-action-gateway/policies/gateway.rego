# The action gateway's decision on one agent tool call: allow, needs_approval or deny, with reasons.
# OPA (Open Policy Agent) runs it; the gateway asks for data.gateway.result. The code decides, never an LLM.
#
# Default policy (v1). The tests in gateway_test.rego are named after these rows.
#   1. Any read-only call                                                 -> allow
#   2. dev or staging namespace, scale change of 100% or less             -> allow
#   3. prod namespace, any write                                          -> needs_approval
#   4. Scale any Deployment to 0 in prod (a scale call or a manifest)     -> deny
#   5. A change freeze is active in prod                                  -> deny, unless the break-glass team
#                                                                            asks; then a break-glass member approves
#   6. A manifest sets privileged: true or hostNetwork, or mounts hostPath -> deny
#   7. The dry run fails, or would violate a PodDisruptionBudget          -> deny
#   8. An unknown tool argument, or the namespace has no env label        -> deny (fail closed)
# Also:
#   - Other dev and staging writes (restart, rollout undo, apply, undo) -> allow; a bigger scale change -> needs_approval
#   - More pods affected than limits.max_pods_affected (the blast radius) -> deny
#   - Only ConfigMap and Deployment manifests, in the namespace the call names -> otherwise deny
#   - Irreversible actions (marked in data.json, or an immutable ConfigMap) -> at least needs_approval
#   - Any other unknown or missing fact (tool, env value, time, team, dry run, replicas, freeze data) -> deny
# Precedence: any deny reason wins, then any approval reason, then allow. If nothing allows the call: deny.
#
# Input: facts the gateway gathers. The agent controls only `tool` and `args`. The rest comes from the
# cluster, the server's own clock and the agent's token.
#   tool       the MCP (Model Context Protocol) tool name
#   args       the tool arguments, exactly as the agent sent them
#   namespace  {"name": ..., "env": <the namespace's env label, absent if it has none>}
#   time       the server's clock, RFC 3339. Never an agent argument
#   team       the requester team, from the agent token
#   current    {"replicas": n}: the Deployment's replicas now (scale_deployment only)
#   dry_run    {"ok": bool, "error": str, "pdb_violations": [names], "pods_affected": n} (writes only)
package gateway

cfg := data.gateway_config

tool := cfg.tools[input.tool]

write if tool.write

env := input.namespace.env

ns_name := object.get(input, ["namespace", "name"], "<none>")

args := input.args if is_object(input.args)

manifest := input.args.manifest if input.tool == "apply_manifest"

# ---------------------------------------------------------------- deny

# Row 8, and every other unknown or missing fact: fail closed.
deny contains sprintf("unknown tool %v (fail closed)", [object.get(input, "tool", "<none>")]) if not tool

deny contains "the tool arguments are not an object (fail closed)" if not args

deny contains sprintf("unknown argument %q (fail closed)", [name]) if {
	tool
	some name, _ in args
	not name in tool.required
	not name in tool.optional
}

deny contains sprintf("missing argument %q (fail closed)", [name]) if {
	some name in tool.required
	not name in object.keys(args)
}

deny contains sprintf("namespace %v has no env label (fail closed)", [ns_name]) if not env

deny contains sprintf("namespace %v has the env label %v, which the policy doesn't know (fail closed)", [ns_name, env]) if {
	not env in {"dev", "staging", "prod"}
}

now := time.parse_rfc3339_ns(input.time)

deny contains "the server time is missing or not RFC 3339 (fail closed)" if not now

deny contains "no requester team (fail closed)" if not valid_team

valid_team if {
	is_string(input.team)
	input.team != ""
}

deny contains "replicas must be a whole number, 0 or more (fail closed)" if {
	input.tool == "scale_deployment"
	not whole_number(input.args.replicas)
}

whole_number(n) if {
	is_number(n)
	n >= 0
	n == floor(n)
}

deny contains "the Deployment's current replica count is unknown (fail closed)" if {
	input.tool == "scale_deployment"
	not current_known
}

# A helper rule: `not is_number(input.current.replicas)` fails, not succeeds, when the field is missing.
current_known if is_number(input.current.replicas)

# Row 7, plus a missing or partial dry-run result.
deny contains concat(": ", array.concat(["dry run failed or incomplete (fail closed)"], dry_run_error)) if {
	write
	not dry_run_complete
}

dry_run_error := [e] if {
	e := input.dry_run.error
	is_string(e)
	e != ""
} else := []

dry_run_complete if {
	input.dry_run.ok == true
	is_array(input.dry_run.pdb_violations)
	is_number(input.dry_run.pods_affected)
}

deny contains sprintf("would violate PodDisruptionBudget %v", [name]) if {
	write
	some name in input.dry_run.pdb_violations
}

deny contains sprintf("blast radius too big: %v pods affected, the limit is %v", [n, cfg.limits.max_pods_affected]) if {
	write
	n := input.dry_run.pods_affected
	n > cfg.limits.max_pods_affected
}

# Row 4. apply_manifest could set replicas to 0 too.
deny contains "scaling to 0 replicas in prod is never allowed" if {
	env == "prod"
	requested_replicas == 0
}

requested_replicas := input.args.replicas if input.tool == "scale_deployment"

requested_replicas := manifest.spec.replicas if manifest.kind == "Deployment"

# Row 5. Freeze times are read in the freeze's own time zone, from the server's clock.
deny contains sprintf("change freeze %q is active in %v (%v); only the %v team can ask to override it", [f.name, env, f.time_zone, cfg.break_glass_team]) if {
	write
	some f in active_freezes
	input.team != cfg.break_glass_team
}

active_freezes contains f if {
	some f in cfg.freezes
	env in f.envs
	in_window(week_minute(now, f.time_zone), week_minute_of(f.start), week_minute_of(f.end))
}

# A typo in the freeze data would silently switch the freeze off, so it denies instead.
deny contains sprintf("change freeze %q has an invalid day, time or time zone in the policy data (fail closed)", [f.name]) if {
	some f in cfg.freezes
	not valid_freeze(f)
}

valid_freeze(f) if {
	week_minute_of(f.start)
	week_minute_of(f.end)
	time.clock([0, f.time_zone])
}

day_index := {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}

# Minutes since Monday 00:00, in time zone tz.
week_minute(ns, tz) := (((day_index[time.weekday([ns, tz])] * 24) + clock[0]) * 60) + clock[1] if {
	clock := time.clock([ns, tz])
}

# "Friday 17:00" -> minutes since Monday 00:00.
week_minute_of(s) := (((day_index[parts[0]] * 24) + to_number(hm[0])) * 60) + to_number(hm[1]) if {
	parts := split(s, " ")
	hm := split(parts[1], ":")
}

in_window(m, start, end) if {
	start <= end
	start <= m
	m < end
}

# The window wraps over the end of the week (e.g. Friday to Monday).
in_window(m, start, end) if {
	start > end
	m >= start
}

in_window(m, start, end) if {
	start > end
	m < end
}

# Row 6. Looks anywhere under spec: containers, init containers, volumes.
deny contains "the manifest sets privileged: true" if {
	walk(object.get(manifest, "spec", {}), [path, value])
	path[count(path) - 1] == "privileged"
	value == true
}

deny contains "the manifest sets hostNetwork: true" if {
	walk(object.get(manifest, "spec", {}), [path, value])
	path[count(path) - 1] == "hostNetwork"
	value == true
}

deny contains "the manifest mounts a hostPath volume" if {
	walk(object.get(manifest, "spec", {}), [path, _])
	path[count(path) - 1] == "hostPath"
}

deny contains "the manifest is not one Kubernetes object (fail closed)" if {
	input.tool == "apply_manifest"
	not is_object(input.args.manifest)
}

deny contains sprintf("kind %v is not allowed; only %v", [kind, concat(", ", cfg.allowed_kinds)]) if {
	kind := object.get(manifest, "kind", "<none>")
	not kind in cfg.allowed_kinds
}

# The env label was checked for the namespace the call names, so the manifest can't point elsewhere.
deny contains sprintf("the manifest names namespace %v, but the call names %v", [manifest.metadata.namespace, input.args.namespace]) if {
	manifest.metadata.namespace != input.args.namespace
}

# ---------------------------------------------------------------- needs approval

# Row 3.
approve contains sprintf("%v is production: every write needs a human approval", [ns_name]) if {
	write
	env == "prod"
}

# Row 2's limit.
approve contains sprintf("a scale change of %v%% is over the %v%% allowed without approval", [round(pct), limit]) if {
	env in {"dev", "staging"}
	pct := scale_change_pct
	limit := cfg.limits.max_scale_change_pct_without_approval
	pct > limit
}

# Measured against at least 1 replica, so scaling from 0 to 1 counts as 100%.
scale_change_pct := (abs(input.args.replicas - current) * 100) / max([current, 1]) if {
	input.tool == "scale_deployment"
	current := input.current.replicas
}

# Row 5's override: the break-glass team asks, and a break-glass member must approve.
break_glass if {
	write
	count(active_freezes) > 0
	input.team == cfg.break_glass_team
}

approve contains sprintf("break-glass change during change freeze %q: a member of the %v team must approve", [f.name, cfg.break_glass_team]) if {
	break_glass
	some f in active_freezes
}

# Undo can't restore these, so they always need a human.
irreversible contains sprintf("%v is marked irreversible in the policy data", [input.tool]) if tool.irreversible

irreversible contains "the manifest sets immutable: true, and undo can't edit an immutable ConfigMap back" if {
	manifest.kind == "ConfigMap"
	manifest.immutable == true
}

approve contains sprintf("irreversible: %v", [reason]) if some reason in irreversible

# ---------------------------------------------------------------- allow

# Row 1.
allow contains "read-only call" if tool.write == false

# Row 2, and the other dev and staging writes.
allow contains sprintf("%v write within the limits", [env]) if {
	write
	env in {"dev", "staging"}
}

# ---------------------------------------------------------------- result

decision := "deny" if count(deny) > 0

else := "needs_approval" if count(approve) > 0

else := "allow" if count(allow) > 0

else := "deny"

reasons := sort(deny) if count(deny) > 0

else := sort(approve) if count(approve) > 0

else := sort(allow) if count(allow) > 0

else := ["no rule allows this call (fail closed)"]

approver_team := cfg.break_glass_team if {
	decision == "needs_approval"
	break_glass
} else := ""

result := {
	"decision": decision,
	"reasons": reasons,
	"irreversible": count(irreversible) > 0,
	"approver_team": approver_team,
	"policy_version": cfg.version,
}
