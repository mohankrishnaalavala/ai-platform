"""The MCP server over real HTTP: token check, tool list, a tool call reaching the gateway with the right
team. Needs the MCP SDK, so CI runs it inside the image; CI's stdlib-only unit-test step skips it."""
import hashlib
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

HAVE_SDK = importlib.util.find_spec("mcp") is not None


class FakeGateway:
    def __init__(self):
        self.calls, self.delay_s = [], 0

    def call(self, tool, args, team):
        self.calls.append((tool, args, team))
        time.sleep(self.delay_s if tool == "restart_deployment" else 0)  # as a call waiting for approval
        return {"decision": "allow", "reasons": ["ok"], "result": {"status": "executed", "error": None}}


@unittest.skipUnless(HAVE_SDK, "needs the MCP SDK (runs inside the image)")
class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        import uvicorn
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({hashlib.sha256(b"agent-token").hexdigest(): "sre"}, f)
        cls.tokens = f.name
        cls.gateway = FakeGateway()
        cls.uv = uvicorn.Server(uvicorn.Config(server.build(cls.gateway, cls.tokens), host="127.0.0.1", port=0, log_level="warning"))
        threading.Thread(target=cls.uv.run, daemon=True).start()
        while not cls.uv.started:
            time.sleep(0.05)
        cls.url = f"http://127.0.0.1:{cls.uv.servers[0].sockets[0].getsockname()[1]}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.uv.should_exit = True
        os.remove(cls.tokens)

    def post(self, headers):
        req = urllib.request.Request(self.url, data=b"{}", method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_refuses_missing_or_unknown_tokens(self):
        self.assertEqual(self.post({}), 401)
        self.assertEqual(self.post({"Authorization": "Bearer wrong"}), 401)

    def test_refuses_other_connection_types(self):  # only HTTP (and the server's lifespan events) get through
        import anyio
        import server
        sent = []

        async def run():
            async def send(message):
                sent.append(message)
            app = server.build(self.gateway, self.tokens)
            await app({"type": "websocket", "headers": [(b"authorization", b"Bearer agent-token")]}, None, send)
        anyio.run(run)
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])

    def test_refuses_a_foreign_host(self):  # DNS-rebinding protection: only 127.0.0.1/localhost, as port-forward sends
        self.assertEqual(self.post({"Authorization": "Bearer agent-token", "Host": "evil.example"}), 421)

    def test_tools_and_a_call(self):
        import anyio
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        async def run():
            http = httpx2.AsyncClient(headers={"Authorization": "Bearer agent-token"}, timeout=30)
            async with Client(streamable_http_client(self.url, http_client=http)) as c:
                tools = {t.name: t for t in (await c.list_tools()).tools}
                r = await c.call_tool("scale_deployment", {"namespace": "dev", "name": "checkout", "replicas": 3, "extra": 1})
                return tools, r
        tools, r = anyio.run(run)
        self.assertEqual(set(tools), {"get_status", "scale_deployment", "restart_deployment", "rollout_undo",
                                      "apply_manifest", "undo_action"})
        self.assertEqual(tools["scale_deployment"].input_schema["required"], ["namespace", "name", "replicas"])
        self.assertEqual(r.structured_content["decision"], "allow")
        self.assertFalse(r.is_error)
        self.assertIn(("scale_deployment", {"namespace": "dev", "name": "checkout", "replicas": 3, "extra": 1}, "sre"),
                      self.gateway.calls)  # raw arguments, unknown ones included, reach the policy

    def test_progress_while_a_call_waits(self):  # keeps an agent's idle timer (Claude Code: 5 minutes) from firing
        import anyio
        import httpx2
        import server
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        seen = []

        async def progress(value, total, message):
            seen.append((value, message))

        async def run():
            http = httpx2.AsyncClient(headers={"Authorization": "Bearer agent-token"}, timeout=30)
            async with Client(streamable_http_client(self.url, http_client=http)) as c:
                return await c.call_tool("restart_deployment", {"namespace": "prod", "name": "checkout"},
                                         progress_callback=progress)
        self.gateway.delay_s = 0.5
        try:
            with mock.patch.object(server, "PROGRESS_S", 0.1):
                r = anyio.run(run)
        finally:
            self.gateway.delay_s = 0
        self.assertEqual(r.structured_content["decision"], "allow")
        self.assertGreaterEqual(len(seen), 3)
        values = [v for v, _ in seen]
        self.assertEqual(values, sorted(set(values)))  # progress only ever grows
        self.assertIn("approval can take 15 minutes", seen[0][1])


if __name__ == "__main__":
    unittest.main()
