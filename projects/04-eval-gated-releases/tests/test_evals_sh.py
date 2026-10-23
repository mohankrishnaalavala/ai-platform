"""scripts/evals.sh (make evals) with stand-ins for kubectl and gcloud (no cluster).
- A self-hosted CONFIG must be the config vLLM serves, or the run stops before any port-forward or call. When they
  match, the script goes on to P3's lib.sh, whose context check the stand-in fails on purpose, so nothing runs.
- An INVALID run (here every answer comes from a fallback) makes it exit 1 after the first of its two runs, and the
  master key, which reaches the gateway, never shows in its output. That run uses a scratch copy of the files the
  script reads, with the test fixtures as golden set v1, so no results land in this checkout.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P4 = "projects/04-eval-gated-releases"
EVALS = HERE.parent / "scripts" / "evals.sh"
MASTER = "sk-fake-master-4c1f9e"
STUBS = {
    "gcloud": f'case "$1 $2" in "config get") echo proj;; "secrets versions") echo "${{STUB_KEY-{MASTER}}}";; esac',
    # Serves STUB_SERVES as vLLM's model-config label. STUB_CONTEXT: kubectl's context (lib.sh checks it).
    # A port-forward runs the fake gateway on the local port.
    "kubectl": 'case "$1 $2" in "get deployment") echo "$STUB_SERVES";; "config current-context") echo "$STUB_CONTEXT";;\n'
               '  port-forward*) echo "port-forward opened" >&2; exec python3 "$FAKE_GATEWAY" "${5%:*}";; esac',
}
# Answers every call, but as a fallback would (x-litellm-attempted-fallbacks 1): every run is INVALID. Without
# the master key it answers 401 instead, so the output shows whether the key got through.
FAKE_GATEWAY = """
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Authorization") != "Bearer THE_KEY":
            self.send_response(401); self.end_headers(); return
        data = json.dumps({"choices": [{"message": {"content": "SCORE: 4"}}], "usage": {}}).encode()
        self.send_response(200)
        self.send_header("x-litellm-model-name", "vertex_ai/gemini-3.5-flash-lite")
        self.send_header("x-litellm-attempted-fallbacks", "1")
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *args):
        pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
""".replace("THE_KEY", MASTER)
# What evals.sh, lib.sh and run.py read, copied into the scratch tree.
FILES = [f"{P4}/scripts/evals.sh", f"{P4}/helm/gateway-canary.yaml", "projects/03-llm-observability/tests/lib.sh",
         "projects/02-llm-gateway/helm/gateway/values.yaml", "projects/01-gpu-serving/bench/prices.json",
         "config/models/qwen3-8b.yaml"] + [str(p.relative_to(ROOT)) for p in (ROOT / P4 / "evals").glob("*.*")]


class EvalsShTest(unittest.TestCase):
    def run_evals(self, tree=ROOT, **env):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name, body in STUBS.items():
            (tmp / name).write_text("#!/bin/bash\n" + body + "\n")
            (tmp / name).chmod(0o755)
        (tmp / "fake_gateway.py").write_text(FAKE_GATEWAY)
        self.fake_gateway = str(tmp / "fake_gateway.py")
        self.addCleanup(subprocess.run, ["pkill", "-f", self.fake_gateway], check=False)  # one that leaked
        env = dict(os.environ, PATH=f"{tmp}:{os.environ['PATH']}", KUBECONFIG=str(tmp / "config"), ZONE="zone",
                   TARGET="chat-default", FAKE_GATEWAY=str(tmp / "fake_gateway.py"),
                   **{"STUB_CONTEXT": "kind-other", **env})
        try:
            run = subprocess.run(["bash", str(tree / P4 / "scripts/evals.sh")], cwd=tree, env=env, capture_output=True,
                                 text=True, timeout=60, check=False)
        except subprocess.TimeoutExpired:  # a port-forward left running keeps the output open
            self.fail("evals.sh didn't finish: a port-forward it opened is still running")
        return run.returncode, run.stdout + run.stderr

    def test_a_config_vllm_doesnt_serve_is_refused(self):
        rc, out = self.run_evals(CONFIG="config/models/qwen3-8b.yaml", STUB_SERVES="qwen2.5-7b-bf16")
        self.assertEqual(rc, 1, out)
        self.assertIn("vLLM serves 'qwen2.5-7b-bf16', not qwen3-8b", out)
        self.assertIn("CONFIG=config/models/qwen3-8b.yaml VLLM_ONLY=1 make deploy LAYERS=01", out)
        self.assertNotIn("port-forward opened", out)

    def test_the_served_config_goes_on_to_the_gateway(self):
        rc, out = self.run_evals(CONFIG="config/models/qwen3-8b.yaml", STUB_SERVES="qwen3-8b")
        self.assertEqual(rc, 1, out)
        self.assertIn("kubectl doesn't point at this session's cluster", out)  # lib.sh, after the served check

    def test_a_hosted_config_skips_the_vllm_check(self):
        rc, out = self.run_evals(CONFIG="projects/02-llm-gateway/helm/gateway/values.yaml", STUB_SERVES="")
        self.assertIn("kubectl doesn't point at this session's cluster", out)
        self.assertEqual(rc, 1, out)

    def test_usage(self):
        rc, out = self.run_evals(CONFIG="no/such/file.yaml", STUB_SERVES="")
        self.assertEqual(rc, 1, out)
        self.assertIn("usage: make evals", out)

    def test_an_exit_inside_lib_sh_stops_the_port_forward(self):
        rc, out = self.run_evals(CONFIG="config/models/qwen3-8b.yaml", STUB_SERVES="qwen3-8b", STUB_KEY="",
                                 STUB_CONTEXT="gke_proj_zone_ai-platform")
        self.assertEqual(rc, 1, out)
        self.assertIn("couldn't read the gateway's master key", out)  # lib.sh exits after its port-forward
        left = subprocess.run(["pgrep", "-f", self.fake_gateway], capture_output=True, text=True, check=False)
        self.assertEqual(left.stdout, "", "the port-forward stand-in is still running")

    def test_an_invalid_run_fails_and_never_shows_the_master_key(self):
        tree = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tree)
        for f in FILES:
            (tree / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / f, tree / f)
        (tree / "projects/02-llm-gateway/tests").mkdir(parents=True)  # lib.sh resolves it
        (tree / P4 / "evals/datasets/v1").mkdir(parents=True)
        shutil.copy2(HERE / "fixtures/dataset/cases.jsonl", tree / P4 / "evals/datasets/v1/cases.jsonl")
        rc, out = self.run_evals(tree, CONFIG="config/models/qwen3-8b.yaml", STUB_SERVES="qwen3-8b",
                                 STUB_CONTEXT="gke_proj_zone_ai-platform")
        self.assertEqual(rc, 1, out)
        self.assertIn("run 1 of 2", out)
        self.assertIn("was answered by a fallback", out)  # so the key reached the gateway (no 401)
        self.assertNotIn("run 2 of 2", out)  # an INVALID run stops the loop
        self.assertNotIn(MASTER, out)
        self.assertEqual(len(list((tree / P4 / "results").glob("*/*.json"))), 1, out)  # still written, for the record


if __name__ == "__main__":
    unittest.main()
