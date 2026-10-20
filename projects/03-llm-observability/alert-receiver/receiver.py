#!/usr/bin/env python3
"""Alertmanager's receiver: it records every alert in every webhook Alertmanager sends, with the time it
arrived, so the game day can measure time-to-alert. Nothing is forwarded anywhere (no Slack, no email).
It runs in the cluster from a ConfigMap (deploy.sh), like P2's mock provider. Alerts hold no prompt text.

Env: PORT (default 8080).
Endpoints:
- POST / (Alertmanager's webhook)
- GET /log: one JSON object per line, one line per alert:
  {"received_at": <unix seconds>, "status": "firing|resolved", "alertname": ..., "labels": {...}, "startsAt": ...}
- GET /health
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []  # in memory: it lasts one session, like the rest of P3's data


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        received_at = time.time()
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            alerts = body.get("alerts", [])
        except (ValueError, AttributeError):
            self._send(400, b'{"error": "not an Alertmanager webhook"}')
            return
        for a in alerts:
            labels = a.get("labels", {})
            LOG.append({"received_at": received_at, "status": a.get("status"), "alertname": labels.get("alertname"),
                        "labels": labels, "startsAt": a.get("startsAt")})
        self._send(200, b'{"status": "ok"}')

    def do_GET(self):
        if self.path == "/log":
            self._send(200, "".join(json.dumps(e) + "\n" for e in LOG).encode(), "application/x-ndjson")
        elif self.path == "/health":
            self._send(200, b'{"status": "ok"}')
        else:
            self._send(404, b'{"error": "not found"}')

    def log_message(self, *args):  # one line per request on stderr is noise here
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
