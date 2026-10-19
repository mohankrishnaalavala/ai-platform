#!/usr/bin/env python3
"""A fake OpenAI-compatible model server for the gateway's tests (test only, never a real route). It
records the body of every chat request it receives, so a test can prove what reached a "provider".
In CI it stands in for both vLLM and the hosted model. It never calls anything.

Env: NAME (in each reply and the log), PORT (default 8000), STREAM_DELAY_S (default 0: the seconds
between streamed events, so a test can tell a real stream from one the gateway buffered).
Endpoints: POST /v1/chat/completions (streaming or not), GET /v1/models, GET /health,
GET /log (every recorded request body, one JSON object per line).
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ.get("NAME", "mock")
STREAM_DELAY_S = float(os.environ.get("STREAM_DELAY_S", "0"))
LOG = []  # in memory: the payloads are fake test data and die with the container
USAGE = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}  # fixed, so budgets are predictable


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": NAME, "object": "model"}]})
        elif self.path == "/log":
            self._send(200, "".join(json.dumps(b) + "\n" for b in LOG).encode(), "application/x-ndjson")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        LOG.append(body)
        text, now = f"reply from {NAME}", int(time.time())
        if not body.get("stream"):
            return self._send(200, {
                "id": f"chatcmpl-{NAME}-{len(LOG)}", "object": "chat.completion", "created": now, "model": NAME,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": USAGE,
            })
        words = text.split(" ")
        chunks = [{"role": "assistant", "content": ""}] + [{"content": w if i == 0 else " " + w} for i, w in enumerate(words)]
        events = [{"id": f"chatcmpl-{NAME}", "object": "chat.completion.chunk", "created": now, "model": NAME,
                   "choices": [{"index": 0, "delta": d, "finish_reason": None}]} for d in chunks]
        events.append({"id": f"chatcmpl-{NAME}", "object": "chat.completion.chunk", "created": now, "model": NAME,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": USAGE})
        # One event at a time, no Content-Length: the connection closes at the end (HTTP/1.0).
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for line in [f"data: {json.dumps(e)}\n\n" for e in events] + ["data: [DONE]\n\n"]:
            self.wfile.write(line.encode())
            self.wfile.flush()
            time.sleep(STREAM_DELAY_S)

    def log_message(self, *args):  # no access log: it would only repeat the paths
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Handler).serve_forever()
