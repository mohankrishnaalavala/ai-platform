#!/usr/bin/env python3
"""The action gateway: an MCP (Model Context Protocol) server over Streamable HTTP. It listens on
127.0.0.1 inside its pod, so it is reached only through kubectl port-forward (other pods can't connect,
and there is no Service). Every request needs an agent token (Authorization: Bearer ...) that maps to a
team; actions.py does the rest.

Env (all optional):
  PORT 8080 · TOKENS_FILE /etc/action-gateway/tokens.json (SHA-256 of token -> team) · OPA_URL (policy.URL)
  AUDIT_BUCKET (cloud) or AUDIT_FILE /data/audit.jsonl (kind) · APPROVER cli · APPROVALS_DIR /data/approvals
  APPROVAL_TIMEOUT_S 900 · RISK_URL (the P2 gateway) · RISK_MODEL chat-hosted · RISK_KEY_FILE /secrets/action-gateway-team-key (no file: "unavailable")
  RISK_TIMEOUT_S 15 · TEST_ONLY_CLOCK_FILE (tests only; see actions.now)"""
import json
import os

import actions
import anyio
import approval
import audit
import kube
import mcp_types as types
import policy
import risk
import uvicorn
from mcp.server import Server


def build(gateway, tokens_file):
    """The ASGI app: a token check in front of the SDK's Streamable HTTP app. The SDK's DNS-rebinding
    protection accepts only Host 127.0.0.1 or localhost, which is what port-forward sends."""
    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(name=n, description=t[0], input_schema=actions.schema(n))
                                            for n, t in actions.TOOLS.items()])

    async def call_tool(ctx, params):
        team = actions.team_for(ctx.request.headers.get("authorization") if ctx.request else None, tokens_file)
        if team is None:
            result = {"decision": "deny", "reasons": ["unknown agent token"]}
        else:  # in a worker thread: an approval can wait 15 minutes, and the cluster calls block
            result = await anyio.to_thread.run_sync(gateway.call, params.name, dict(params.arguments or {}), team)
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, indent=2))],
                                    structured_content=result,
                                    is_error=(result.get("result") or {}).get("status") == "failed")

    app = Server("action-gateway", version="0.1.0", on_list_tools=list_tools,
                 on_call_tool=call_tool).streamable_http_app(host="127.0.0.1")

    async def require_token(scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
            if actions.team_for(auth, tokens_file) is None:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error": "unknown agent token"}'})
                return
        await app(scope, receive, send)
    return require_token


def gateway_from_env():
    e = os.environ.get
    if e("APPROVER", "cli") != "cli":
        raise SystemExit(f"unknown APPROVER {e('APPROVER')!r}: only cli so far")
    return actions.Gateway(
        kube=kube.Kube(),
        audit_log=audit.GcsAudit(e("AUDIT_BUCKET")) if e("AUDIT_BUCKET") else audit.FileAudit(e("AUDIT_FILE", "/data/audit.jsonl")),
        approver=approval.CliApprover(e("APPROVALS_DIR", "/data/approvals")),
        decide=lambda facts: policy.decide(facts, e("OPA_URL", policy.URL)),
        summarize=lambda facts: risk.summary(
            facts, e("RISK_URL", "http://gateway.gateway.svc.cluster.local:4000/v1/chat/completions"),
            e("RISK_MODEL", "chat-hosted"), e("RISK_KEY_FILE", "/secrets/action-gateway-team-key"), float(e("RISK_TIMEOUT_S", "15"))),
        approval_timeout_s=float(e("APPROVAL_TIMEOUT_S", "900")))


if __name__ == "__main__":
    uvicorn.run(build(gateway_from_env(), os.environ.get("TOKENS_FILE", "/etc/action-gateway/tokens.json")),
                host="127.0.0.1", port=int(os.environ.get("PORT", "8080")), log_level="warning")
