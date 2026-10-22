"""The action pipeline behind every tool:

  dry run as the scoped ServiceAccount (diff, blast radius) -> policy (OPA) ->
  if needs_approval: risk summary, then a person's answer (auto-reject after 15 minutes) ->
  act -> audit record, with an undo token

undo_action runs the same pipeline on the change that restores the object. Standard library only.

The facts the policy gets (OPA input; policies/gateway.rego reads the same names):
  tool       the MCP tool name
  args       the tool arguments exactly as the agent sent them, unknown ones included
  namespace  {"name": ..., "env": the namespace's env label}; "env" is absent if it has none or can't be read
  time       the server's own clock, RFC 3339 in UTC. Never an agent argument
  team       the requester team, from the agent's token
  current    {"replicas": n}: the Deployment's replicas now (when the call targets an existing Deployment)
  dry_run    {"ok": bool, "error": text, "pods_affected": n, "capacity_change_pct": n or null,
              "pdb_violations": [PDB names]} (writes only; just ok and error when it failed)
The policy answers {"decision", "reasons", "policy_version", "approver_team", "irreversible"} (policy.py).

The agent controls only `tool` and `args`. An argument that would be unsafe to put in a cluster request (a
missing one, a wrong type, a name that isn't a Kubernetes name) means nothing is sent to the cluster: the
dry run is marked failed, and the policy gives the reasons."""
import datetime
import hashlib
import json
import os
import re
import secrets
import sys

import audit
import dryrun
import kube
import risk
from kube import KubeError

NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")  # a Kubernetes object or namespace name
TYPES = {"string": str, "integer": int, "object": dict}
_NS = {"type": "string", "description": "Target namespace: dev, staging or prod."}
_DEP = {"type": "string", "description": "Deployment name."}
_WRITE = (" Every change is dry-run and checked against policy first, and may wait up to 15 minutes for a "
          "person to approve it. The result has an action_id for undo_action.")
# name: (description the agent reads, properties, required). Facts only, never policy details: the agent reads them.
TOOLS = {
    "get_status": (("Read-only: replicas, readiness and revision of the Deployments in a namespace (or of one), "
                    "and its PodDisruptionBudgets."), {"namespace": _NS, "name": _DEP}, ["namespace"]),
    "scale_deployment": ("Set a Deployment's replica count." + _WRITE,
                         {"namespace": _NS, "name": _DEP, "replicas": {"type": "integer", "minimum": 0}},
                         ["namespace", "name", "replicas"]),
    "restart_deployment": ("Restart a Deployment's pods with a rolling update, like kubectl rollout restart." + _WRITE,
                           {"namespace": _NS, "name": _DEP}, ["namespace", "name"]),
    "rollout_undo": ("Roll a Deployment back to its previous revision, or to to_revision, like kubectl rollout undo." + _WRITE,
                     {"namespace": _NS, "name": _DEP, "to_revision": {"type": "integer", "minimum": 1}},
                     ["namespace", "name"]),
    "apply_manifest": ("Server-side apply of one ConfigMap (v1) or Deployment (apps/v1), given as a JSON object." + _WRITE,
                       {"namespace": _NS, "manifest": {"type": "object"}}, ["namespace", "manifest"]),
    "undo_action": ("Undo an earlier action of your team by its action_id: the object goes back to how it was "
                    "before (or is deleted, if that action created it)." + _WRITE,
                    {"action_id": {"type": "string"}}, ["action_id"]),
}
PDBS = "/apis/policy/v1/namespaces/{ns}/poddisruptionbudgets"
RESPONSE = ("action_id", "decision", "reasons", "policy_version", "diff", "risk_summary", "approver", "result")


class Refused(Exception):
    """An undo the server turns down before the policy: unknown, another team's, or overtaken by a later change."""


class NotRun(Exception):
    """The dry run couldn't be sent: unsafe arguments or an unsupported kind."""


def schema(tool):
    _, props, required = TOOLS[tool]
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def unsafe_args(tool, args):
    """Why these arguments can't go into a cluster request (empty if they can). Unknown argument names are
    the policy's to judge, not a reason here."""
    _, props, required = TOOLS[tool]
    errors = [f"missing argument {k}" for k in required if k not in args]
    for k, v in args.items():
        t = props.get(k, {}).get("type")
        if t and (not isinstance(v, TYPES[t]) or (t == "integer" and isinstance(v, bool))):  # True is an int in Python
            errors.append(f"{k} must be a {t}")
        elif t == "integer" and v < props[k]["minimum"]:
            errors.append(f"{k} must be {props[k]['minimum']} or more")
    for k in ("namespace", "name"):
        if isinstance(args.get(k), str) and not NAME.match(args[k]):
            errors.append(f"{k} is not a valid Kubernetes name")
    if isinstance(args.get("action_id"), str) and not audit.ID.match(args["action_id"]):
        errors.append("action_id is not an action id")
    m = args.get("manifest")
    if isinstance(m, dict):
        name = m.get("metadata", {}).get("name") if isinstance(m.get("metadata"), dict) else None
        if not isinstance(name, str) or not NAME.match(name):
            errors.append("the manifest needs a valid metadata.name")
    return errors


def team_for(authorization, tokens_file):
    """The team an agent token belongs to, or None. The file maps SHA-256 hashes of tokens to teams (a
    ConfigMap; read on every call, so a new session's tokens work without a restart)."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        with open(tokens_file) as f:
            teams = json.load(f)
    except (OSError, ValueError):
        return None
    team = teams.get(hashlib.sha256(token.strip().encode()).hexdigest()) if isinstance(teams, dict) else None
    return team if isinstance(team, str) and team else None


def now():
    """The server's own clock. TEST_ONLY_CLOCK_FILE (set only by tests, never by the Helm chart) names a
    file holding an RFC 3339 time to use instead, so tests can fix the time. No tool argument can set it."""
    path = os.environ.get("TEST_ONLY_CLOCK_FILE")
    if path:
        try:
            with open(path) as f:
                text = f.read().strip()
        except FileNotFoundError:
            text = ""
        if text:
            return datetime.datetime.fromisoformat(text).astimezone(datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc)


def _rfc3339(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(d):
    s = d.get("status") or {}
    return {"name": d["metadata"]["name"], "replicas": d["spec"].get("replicas"),
            "ready": s.get("readyReplicas", 0), "available": s.get("availableReplicas", 0),
            "updated": s.get("updatedReplicas", 0),
            "revision": (d["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision"),
            "images": [c.get("image") for c in d["spec"]["template"]["spec"].get("containers", [])],
            "conditions": {c["type"]: c["status"] for c in s.get("conditions", [])}}


def _revision(obj):
    return int((obj["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision", 0))


class Gateway:
    def __init__(self, kube, audit_log, approver, decide, summarize, approval_timeout_s=900.0, clock=now):
        self.kube, self.audit, self.approver = kube, audit_log, approver
        self.decide, self.summarize = decide, summarize
        self.approval_timeout_s, self.clock = approval_timeout_s, clock

    def call(self, tool, args, team):
        """One tool call from an agent of `team`. Returns the result as a dict."""
        if tool not in TOOLS:
            return {"decision": "deny", "reasons": [f"unknown tool {tool}"]}
        if tool == "get_status":
            return self.status(args, team)
        return self.act(tool, args, team)

    def facts(self, tool, args, ns, team, when):
        namespace = {"name": ns}
        env = self.kube.namespace_env(ns) if isinstance(ns, str) and NAME.match(ns) else None
        if env is not None:
            namespace["env"] = env
        return {"tool": tool, "args": args, "namespace": namespace, "time": _rfc3339(when), "team": team}

    def status(self, args, team):
        ns = args.get("namespace")
        d = self.decide(self.facts("get_status", args, ns, team, self.clock()))
        if d["decision"] != "allow" or unsafe_args("get_status", args):
            return d
        try:
            deps = ([self.kube.get("Deployment", ns, args["name"])] if "name" in args
                    else self.kube.items(f"/apis/apps/v1/namespaces/{ns}/deployments"))
            pdbs = self.kube.items(PDBS.format(ns=ns))
        except KubeError as e:
            return d | {"error": str(e)}
        return d | {"deployments": [_status(x) for x in deps if x],
                    "pod_disruption_budgets": [{"name": p["metadata"]["name"],
                                                "disruptions_allowed": (p.get("status") or {}).get("disruptionsAllowed"),
                                                "current_healthy": (p.get("status") or {}).get("currentHealthy"),
                                                "desired_healthy": (p.get("status") or {}).get("desiredHealthy")}
                                               for p in pdbs]}

    def target(self, tool, args, team):
        """(namespace, kind, name, the record being undone or None), from the (safe) arguments alone."""
        if tool == "undo_action":
            rec = self.audit.read(args["action_id"])
            if rec is None:
                raise Refused(f"no action {args['action_id']}")
            if rec.get("team") != team:
                raise Refused("only the team that made an action can undo it")
            tok = rec.get("undo_token")
            if not tok:
                raise Refused("that action changed nothing that undo can restore")
            return tok["namespace"], tok["kind"], tok["name"], rec
        if tool == "apply_manifest":
            m = args["manifest"]
            return args["namespace"], m.get("kind"), m["metadata"]["name"], None
        return args["namespace"], "Deployment", args["name"], None

    def change(self, tool, args, ns, kind, before, undone, when):
        """The change to send, built from the object as it is now."""
        if tool == "scale_deployment":
            return {"type": "merge", "body": {"spec": {"replicas": args["replicas"]}}}
        if tool == "restart_deployment":
            return {"type": "merge", "body": {"spec": {"template": {"metadata": {"annotations": {
                "kubectl.kubernetes.io/restartedAt": _rfc3339(when)}}}}}}
        if tool == "apply_manifest":  # sent as given: a different metadata.namespace fails the dry run
            m = args["manifest"]
            return {"type": "apply", "body": m | {"metadata": {"namespace": ns} | m["metadata"]}}
        if tool == "rollout_undo":
            if before is None:
                raise NotRun("Deployment not found")
            # Until the controller records the last change, "previous" would be one revision too far back.
            if (before.get("status") or {}).get("observedGeneration", 0) < before["metadata"].get("generation", 0):
                raise NotRun("the Deployment's last change isn't recorded yet; try again in a few seconds")
            wanted = args.get("to_revision")
            old = [rs for rs in self.kube.items(f"/apis/apps/v1/namespaces/{ns}/replicasets")
                   if any(o.get("uid") == before["metadata"]["uid"] for o in rs["metadata"].get("ownerReferences", []))
                   and (_revision(rs) == wanted if wanted else _revision(rs) < _revision(before))]
            if not old:
                raise NotRun(f"no revision {wanted}" if wanted else "no previous revision to roll back to")
            template = max(old, key=_revision)["spec"]["template"]
            template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)  # as kubectl rollout undo
            return {"type": "json", "body": [{"op": "replace", "path": "/spec/template", "value": template}]}
        # undo_action
        if dryrun.version(kind, before) != dryrun.version(kind, undone["after"]):
            raise Refused("the object changed after that action; undo would overwrite someone else's change")
        tok = undone["undo_token"]
        if tok["op"] == "delete":
            return {"type": "delete", "body": {"preconditions": {"uid": tok["uid"]}}}
        if kind == "Deployment":
            return {"type": "json", "body": [{"op": "replace", "path": "/spec", "value": undone["before"]["spec"]}]}
        # shortcut: restores a ConfigMap's data only, not binaryData, labels or annotations; add them if a scenario needs it
        return {"type": "json", "body": [{"op": "add", "path": "/data", "value": undone["before"].get("data", {})}]}

    def act(self, tool, args, team):
        when = self.clock()
        rec = {"action_id": when.strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4), "time": _rfc3339(when),
               "team": team, "request": {"tool": tool, "arguments": args}, "facts": None, "diff": "",
               "before": None, "after": None, "risk_summary": None, "approver": None, "undo_token": None,
               "undo_of": args.get("action_id") if tool == "undo_action" else None}
        unsafe = unsafe_args(tool, args)
        try:
            ns, kind, name, undone = (args.get("namespace"), None, None, None) if unsafe else self.target(tool, args, team)
            facts = rec["facts"] = self.facts(tool, args, ns, team, when)
            before = change = None
            try:
                if unsafe:
                    raise NotRun("; ".join(unsafe))
                if not isinstance(kind, str) or kind not in kube.PATHS:
                    raise NotRun(f"kind {kind} is not supported")
                before = self.kube.get(kind, ns, name)
                rec["before"] = dryrun.snapshot(before)
                if kind == "Deployment" and before:
                    facts["current"] = {"replicas": before["spec"].get("replicas", 1)}
                change = self.change(tool, args, ns, kind, before, undone, when)
                preview = self.kube.send(kind, ns, name, change, dry_run=True)
                pdbs = self.kube.items(PDBS.format(ns=ns)) if kind == "Deployment" else []
                facts["dry_run"] = {"ok": True, "error": ""} | dryrun.blast_radius(kind, before, preview, pdbs)
                rec["diff"] = dryrun.diff(before, preview)
            except (KubeError, NotRun) as e:
                facts["dry_run"] = {"ok": False, "error": f"not run: {e}" if isinstance(e, NotRun) else str(e)}
        except Refused as e:
            rec.update(decision="deny", reasons=list(e.args), policy_version="none: refused before the policy check")
            return self.finish(rec, "denied")

        rec.update(self.decide(facts))
        if rec["decision"] == "deny" or not facts["dry_run"]["ok"]:  # never act on a failed dry run
            return self.finish(rec, "denied")
        if rec["decision"] == "needs_approval":
            rec["risk_summary"] = self.summarize(risk.safe_facts(facts, kind, name, rec))
            rec["approver"] = self.approver.decide(rec["action_id"], {
                "action_id": rec["action_id"], "team": team, "tool": tool, "namespace": ns, "kind": kind, "name": name,
                "reasons": rec["reasons"], "approver_team": rec.get("approver_team", ""),
                "irreversible": rec.get("irreversible", False), "dry_run": facts["dry_run"], "diff": rec["diff"],
                "ai_risk_summary": rec["risk_summary"],  # the LLM explains; the policy above decided
                "auto_reject_after_s": self.approval_timeout_s}, self.approval_timeout_s)
            if not rec["approver"]["approved"]:
                return self.finish(rec, "rejected")
        try:
            if dryrun.version(kind, self.kube.get(kind, ns, name)) != dryrun.version(kind, before):
                return self.finish(rec, "failed", "the object changed after the dry run; nothing was done")
            after = self.kube.send(kind, ns, name, change, dry_run=False)
        except KubeError as e:
            return self.finish(rec, "failed", str(e))
        rec["after"] = dryrun.snapshot(after)
        if after is not None:  # a delete (undo of a create) can't be undone: re-creating is out of v1
            rec["undo_token"] = {"op": "restore" if before else "delete", "kind": kind, "namespace": ns, "name": name}
            if before is None:
                rec["undo_token"]["uid"] = after["metadata"]["uid"]
        return self.finish(rec, "executed")

    def finish(self, rec, status, error=None):
        rec["result"] = {"status": status, "error": error}
        try:
            self.audit.write(rec)
        except Exception as e:  # noqa: BLE001 - the action may have happened, so say so; the log line holds no object contents
            rec["result"]["audit_error"] = f"the audit record was not written ({type(e).__name__})"
        print(json.dumps({"action_id": rec["action_id"], "team": rec["team"], "tool": rec["request"]["tool"],
                          "decision": rec.get("decision"), "result": rec["result"]}), file=sys.stderr, flush=True)
        return {k: rec.get(k) for k in RESPONSE} | {"dry_run": (rec.get("facts") or {}).get("dry_run")}
