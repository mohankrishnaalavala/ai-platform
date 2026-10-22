#!/usr/bin/env python3
"""The action gateway: an MCP (Model Context Protocol) server over Streamable HTTP. It listens on
127.0.0.1 inside its pod, so it is reached only through kubectl port-forward (other pods can't connect,
and there is no Service). Every request needs an agent token (Authorization: Bearer ...) that maps to a
team; actions.py does the rest.

Env (all optional):
  PORT 8080 · TOKENS_FILE /etc/action-gateway/tokens.json (SHA-256 of token -> team) · OPA_URL (policy.URL)
  AUDIT_BUCKET (cloud) or AUDIT_FILE /data/audit.jsonl (kind) · APPROVAL_TIMEOUT_S 900
  APPROVER cli (kind and CI; APPROVALS_DIR /data/approvals) or slack (the cluster; SLACK_CHANNEL, SLACK_APPROVERS
    as JSON, SLACK_APP_TOKEN_FILE /secrets/slack-app-token, SLACK_BOT_TOKEN_FILE /secrets/slack-bot-token)
  RISK_URL (the P2 gateway) · RISK_MODEL chat-hosted · RISK_KEY_FILE /secrets/action-gateway-team-key (no file: "unavailable")
  RISK_TIMEOUT_S 15 · TEST_ONLY_CLOCK_FILE (tests only; see actions.now)

Threads: each tool call runs in a worker thread, at most THREADS (40) at once. A call waiting for a person
holds its thread for up to 15 minutes, so at most MAX_PENDING (16) approvals wait at once and the next one
is rejected at once (approval.Bounded). That leaves at least 24 threads for every other call.

Long waits: while a call runs, the server sends an MCP progress notification every PROGRESS_S (30) seconds,
if the agent's request asked for progress. Each one resets the agent's idle timer (Claude Code's is 5
minutes for HTTP servers), so a 15-minute approval wait isn't cut off."""
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
import slack_approval
import uvicorn
from mcp.server import Server

THREADS, MAX_PENDING, PROGRESS_S = 40, 16, 30.0
CALLS = anyio.CapacityLimiter(THREADS)


async def keep_alive(ctx):
    """Progress every PROGRESS_S seconds, counting the seconds waited (it must only ever grow). A no-op if
    the agent sent no progressToken."""
    waited = 0.0
    while True:
        await anyio.sleep(PROGRESS_S)
        waited += PROGRESS_S
        try:
            await ctx.session.report_progress(waited, message=f"still working: {waited:g} s (an approval can take 15 minutes)")
        except Exception:  # noqa: BLE001 - the agent went away: stop telling it, but the call goes on (and is audited)
            return


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
            async with anyio.create_task_group() as tg:
                tg.start_soon(keep_alive, ctx)
                result = await anyio.to_thread.run_sync(gateway.call, params.name, dict(params.arguments or {}), team,
                                                        limiter=CALLS)
                tg.cancel_scope.cancel()
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, indent=2))],
                                    structured_content=result,
                                    is_error=(result.get("result") or {}).get("status") == "failed")

    app = Server("action-gateway", version="0.1.0", on_list_tools=list_tools,
                 on_call_tool=call_tool).streamable_http_app(host="127.0.0.1")

    async def require_token(scope, receive, send):
        if scope["type"] not in ("http", "lifespan"):  # e.g. a WebSocket: refused before the handshake
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
            if actions.team_for(auth, tokens_file) is None:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error": "unknown agent token"}'})
                return
        await app(scope, receive, send)
    return require_token


def _read(path):
    with open(path) as f:
        return f.read().strip()


def gateway_from_env():
    e = os.environ.get
    audit_log = audit.GcsAudit(e("AUDIT_BUCKET")) if e("AUDIT_BUCKET") else audit.FileAudit(e("AUDIT_FILE", "/data/audit.jsonl"))
    if e("APPROVER", "cli") == "cli":
        approver = approval.CliApprover(e("APPROVALS_DIR", "/data/approvals"))
    elif e("APPROVER") == "slack":  # tokens from the mounted files, straight to Slack's client; never logged
        approver = slack_approval.connect(_read(e("SLACK_APP_TOKEN_FILE", "/secrets/slack-app-token")),
                                          _read(e("SLACK_BOT_TOKEN_FILE", "/secrets/slack-bot-token")),
                                          e("SLACK_CHANNEL"), json.loads(e("SLACK_APPROVERS") or "{}"), audit_log)
    else:
        raise SystemExit(f"unknown APPROVER {e('APPROVER')!r}: cli or slack")
    return actions.Gateway(
        kube=kube.Kube(),
        audit_log=audit_log,
        approver=approval.Bounded(approver, MAX_PENDING),
        decide=lambda facts: policy.decide(facts, e("OPA_URL", policy.URL)),
        summarize=lambda facts: risk.summary(
            facts, e("RISK_URL", "http://gateway.gateway.svc.cluster.local:4000/v1/chat/completions"),
            e("RISK_MODEL", "chat-hosted"), e("RISK_KEY_FILE", "/secrets/action-gateway-team-key"), float(e("RISK_TIMEOUT_S", "15"))),
        approval_timeout_s=float(e("APPROVAL_TIMEOUT_S", "900")))


if __name__ == "__main__":
    uvicorn.run(build(gateway_from_env(), os.environ.get("TOKENS_FILE", "/etc/action-gateway/tokens.json")),
                host="127.0.0.1", port=int(os.environ.get("PORT", "8080")), log_level="warning")
