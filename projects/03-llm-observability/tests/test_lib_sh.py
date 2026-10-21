"""lib.sh's port-forward helpers, with stand-ins for kubectl and gcloud (no cluster). A port-forward reopened
right after stop_port_forwards must still answer: in P3's obs-scale run (2026-10-21) the old one still held the
port, the new one failed to bind, and the old one answered the readiness check before it went.
"""
import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest

LIB = pathlib.Path(__file__).with_name("lib.sh")

# Serves the port like kubectl port-forward, and keeps it for 1.5 s after SIGTERM, as a slow exit would.
SERVER = textwrap.dedent("""
    import http.server, signal, sys, time
    signal.signal(signal.SIGTERM, lambda *_: (time.sleep(1.5), sys.exit(0)))
    http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), http.server.SimpleHTTPRequestHandler).serve_forever()
""")


class PortForwardTest(unittest.TestCase):
    def test_a_reopened_port_forward_still_answers(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "server.py").write_text(SERVER)
            stubs = {
                "gcloud": 'case "$*" in "config get project") echo proj;; secrets*) echo sk-stub;; esac',
                "kubectl": 'case "$1 $2" in "config current-context") echo gke_proj_zone_ai-platform;;\n'
                           f'  port-forward*) exec python3 {d}/server.py "${{5%:*}}" 2>/dev/null;; esac',
            }
            for name, body in stubs.items():
                (d / name).write_text("#!/bin/bash\n" + body + "\n")
                (d / name).chmod(0o755)
            script = (f'set -euo pipefail; ZONE=zone; source "{LIB}"; trap stop_port_forwards EXIT\n'
                      "stop_port_forwards; port_forward gateway/gateway 14300:4000; sleep 3\n"
                      "curl -sf -o /dev/null --max-time 2 http://127.0.0.1:14300/\n")
            env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", KUBECONFIG=str(d / "config"))
            run = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(run.returncode, 0, f"nothing answered on 14300 after the reopen: {run.stdout}{run.stderr}")


if __name__ == "__main__":
    unittest.main()
