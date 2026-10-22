"""scripts/evals.sh (make evals) with stand-ins for kubectl and gcloud (no cluster): a self-hosted CONFIG must be
the config vLLM serves, or the run stops before any port-forward or call. When they match, the script goes on to
P3's lib.sh, whose context check the stand-in fails on purpose, so nothing runs.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EVALS = HERE.parent / "scripts" / "evals.sh"
STUBS = {
    "gcloud": '[ "$1 $2" != "config get" ] || echo proj',
    # Serves STUB_SERVES as vLLM's model-config label; any other context than this session's cluster.
    "kubectl": 'case "$1 $2" in "get deployment") echo "$STUB_SERVES";; "config current-context") echo kind-other;;\n'
               '  port-forward*) echo "port-forward opened" >&2; exit 1;; esac',
}


class EvalsShTest(unittest.TestCase):
    def run_evals(self, **env):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for name, body in STUBS.items():
            (tmp / name).write_text("#!/bin/bash\n" + body + "\n")
            (tmp / name).chmod(0o755)
        env = dict(os.environ, PATH=f"{tmp}:{os.environ['PATH']}", KUBECONFIG=str(tmp / "config"), ZONE="zone",
                   TARGET="chat-default", **env)
        run = subprocess.run(["bash", str(EVALS)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
                             check=False)
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


if __name__ == "__main__":
    unittest.main()
