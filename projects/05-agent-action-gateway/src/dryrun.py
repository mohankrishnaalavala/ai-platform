"""Dry-run helpers, pure functions: the diff an approver reads, the blast radius the policy checks
(pods affected, capacity change %, PodDisruptionBudget (PDB) violations), and the version that tells
whether someone changed an object in the meantime."""
import difflib
import json
import math

NOISE = ("managedFields", "resourceVersion", "uid", "creationTimestamp", "generation")


def snapshot(obj):
    """The object as the audit record keeps it: all of it but managedFields (bulky bookkeeping)."""
    if obj is None:
        return None
    obj = json.loads(json.dumps(obj))
    obj.get("metadata", {}).pop("managedFields", None)
    return obj


def diff(before, after):
    """A unified diff of spec, data and labels: what the change does, without status or bookkeeping."""
    def lines(obj):
        if obj is None:
            return []
        obj = snapshot(obj)
        obj.pop("status", None)
        for k in NOISE:
            obj.get("metadata", {}).pop(k, None)
        return json.dumps(obj, indent=2, sort_keys=True).splitlines()
    return "\n".join(difflib.unified_diff(lines(before), lines(after), "before", "after", lineterm=""))


def version(kind, obj):
    """Changes only when someone changes the object: a Deployment's generation (status updates don't
    bump it), or any other object's resourceVersion. None if the object doesn't exist."""
    if obj is None:
        return None
    return obj["metadata"].get("generation" if kind == "Deployment" else "resourceVersion")


def _resolve(value, total, round_up):
    """An int, or a percentage of total, as Kubernetes resolves maxSurge (rounded up) and maxUnavailable (down)."""
    if isinstance(value, str) and value.endswith("%"):
        x = int(value[:-1]) * total / 100
        return math.ceil(x) if round_up else math.floor(x)
    return int(value)


def down_at_once(dep):
    """How many pods a rollout of this Deployment takes down at the same time."""
    spec = dep["spec"]
    n = spec.get("replicas", 1)
    strategy = spec.get("strategy") or {}
    if strategy.get("type") == "Recreate":
        return n
    ru = strategy.get("rollingUpdate") or {}
    down = _resolve(ru.get("maxUnavailable", "25%"), n, False)
    up = _resolve(ru.get("maxSurge", "25%"), n, True)
    return 1 if down == 0 and up == 0 else min(down, n)  # Kubernetes' own rule when both round to 0


def selects(selector, labels):
    """Whether a label selector (matchLabels, matchExpressions) matches these labels."""
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for e in selector.get("matchExpressions") or []:
        op, has, value = e["operator"], e["key"] in labels, labels.get(e["key"])
        if ((op == "In" and value not in e.get("values", [])) or (op == "NotIn" and has and value in e.get("values", []))
                or (op == "Exists" and not has) or (op == "DoesNotExist" and has)):
            return False
    return True


def _status(pdb, field):
    return (pdb.get("status") or {}).get(field, 0)


def blast_radius(kind, before, after, pdbs):
    """What a change from before to after does to pods. Either object may be None (create, delete).

    A PodDisruptionBudget (PDB) is violated when the change would leave fewer ready pods than the budget
    allows: its ready pods (status.currentHealthy) minus the pods taken down at once, below
    status.desiredHealthy. Kubernetes enforces budgets only on evictions, never on a scale-down or a
    rollout, so this check is the gateway's own. A budget without a status counts as 0 ready (fail closed)."""
    if kind != "Deployment":
        return {"pods_affected": 0, "capacity_change_pct": 0.0, "pdb_violations": []}
    b = before["spec"].get("replicas", 1) if before else 0
    a = after["spec"].get("replicas", 1) if after else 0
    rollout = before is not None and after is not None and before["spec"]["template"] != after["spec"]["template"]
    disrupted = max(b - a, 0) + (down_at_once(after) if rollout else 0)
    labels = (before or after)["spec"]["template"].get("metadata", {}).get("labels") or {}
    violations = sorted(p["metadata"]["name"] for p in pdbs
                        if disrupted and p["spec"].get("selector") is not None and selects(p["spec"]["selector"], labels)
                        and _status(p, "currentHealthy") - disrupted < _status(p, "desiredHealthy"))
    return {"pods_affected": max(a, b) if rollout else abs(a - b),  # a rollout replaces every pod
            "capacity_change_pct": None if b == 0 else round((a - b) * 100 / b, 1),
            "pdb_violations": violations}
