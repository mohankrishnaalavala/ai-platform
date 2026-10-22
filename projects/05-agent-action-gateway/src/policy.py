"""Asks OPA (Open Policy Agent, the sidecar on 127.0.0.1:8181) for a decision on the structured facts
(see actions.py). Fails closed: no answer, a malformed answer or an unknown decision is a deny."""
import json
import urllib.request

URL = "http://127.0.0.1:8181/v1/data/gateway/result"
DECISIONS = ("allow", "needs_approval", "deny")


def decide(facts, url=URL, timeout=5, urlopen=urllib.request.urlopen):
    """{"decision": allow|needs_approval|deny, "reasons": [...], "policy_version": ..., "approver_team": the
    team whose member must approve ("" for anyone), "irreversible": bool}"""
    try:
        req = urllib.request.Request(url, data=json.dumps({"input": facts}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=timeout) as r:
            result = json.loads(r.read())["result"]
        decision, reasons = result["decision"], result["reasons"]
        if decision not in DECISIONS or not isinstance(reasons, list):
            raise ValueError(f"unexpected decision {decision!r}")
        return {"decision": decision, "reasons": [str(x) for x in reasons],
                "policy_version": str(result.get("policy_version", "unknown")),
                "approver_team": str(result.get("approver_team") or ""), "irreversible": result.get("irreversible") is True}
    except Exception as e:  # noqa: BLE001 - any failure at all is a deny (fail closed)
        return {"decision": "deny", "reasons": [f"policy engine unavailable or its answer invalid ({type(e).__name__})"],
                "policy_version": "unknown", "approver_team": "", "irreversible": False}
