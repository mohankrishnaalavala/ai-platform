"""deploy.sh's VLLM_ONLY=1, the model swap in P4's cluster window, with stand-ins for gcloud, kubectl and helm (no
cluster). The stand-in helm logs every call; for vLLM's release it also renders, with the real helm when there is
one, exactly what the upgrade would install.
- VLLM_ONLY=1 makes no Prometheus or KEDA call and changes no namespace: those would put back P1's values alone and
  drop P3's monitoring.
- Each swap's values come fresh from its CONFIG (no reused values): after Qwen3-8B, Qwen2.5-1.5B renders without
  Qwen3's thinking-off setting. The Recreate strategy stops the old pod before the new one starts (one GPU).
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEPLOY = HERE.parent / "deploy.sh"
SWAPS = ["qwen2.5-7b-bf16", "qwen3-8b", "qwen2.5-1.5b"]  # the window's order, ending on the bad candidate
REAL_HELM = shutil.which("helm")

STUBS = {
    "gcloud": 'case "$1 $2" in "config get") echo proj;; "projects describe") echo own;;\n'
              '  "container clusters") echo 20261022t0000z;; esac',
    "kubectl": 'echo "$*" >> "$LOG/kubectl"; [ "$1 $2" != "config current-context" ] || echo gke_proj_zone_ai-platform',
    "helm": 'echo "$*" >> "$LOG/helm"\n'
            'if [ "$1 $2 $3" = "upgrade --install vllm" ] && [ -n "$REAL_HELM" ]; then\n'
            '  shift 3; exec "$REAL_HELM" template vllm "$@" > "$LOG/render.yaml"\nfi',
}


class VllmOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        for name, body in STUBS.items():
            (self.tmp / name).write_text("#!/bin/bash\n" + body + "\n")
            (self.tmp / name).chmod(0o755)

    def deploy(self, config, **env):
        log = self.tmp / "log"  # the calls and the render, apart from the stand-ins themselves
        shutil.rmtree(log, ignore_errors=True)
        log.mkdir()
        env = dict(os.environ, PATH=f"{self.tmp}:{os.environ['PATH']}", LOG=str(log), REAL_HELM=REAL_HELM or "",
                   KUBECONFIG=str(self.tmp / "config"), ZONE="zone", CONFIG=f"config/models/{config}.yaml", **env)
        run = subprocess.run(["bash", str(DEPLOY)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
                             check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return {f: (log / f).read_text() if (log / f).exists() else "" for f in ("helm", "kubectl", "render.yaml")}

    def test_vllm_only_calls_helm_for_vllm_alone(self):
        for config in SWAPS:
            with self.subTest(config=config):
                out = self.deploy(config, VLLM_ONLY="1")
                calls = out["helm"].splitlines()
                self.assertEqual(len(calls), 1, calls)
                self.assertTrue(calls[0].startswith("upgrade --install vllm "), calls)
                self.assertNotRegex(out["helm"], r"prometheus|keda|reuse-values")
                self.assertIn(f"config/models/{config}.yaml", calls[0])
                self.assertNotRegex(out["kubectl"], r"(?m)^(apply|label) ")  # no namespace change

    def test_full_deploy_still_installs_prometheus_and_keda_first(self):
        calls = [c.split()[2] for c in self.deploy("qwen2.5-7b-bf16")["helm"].splitlines()]
        self.assertEqual(calls, ["prometheus", "keda", "vllm"])

    @unittest.skipUnless(REAL_HELM, "needs helm to render the chart")
    def test_each_swap_renders_from_its_own_config(self):
        renders = {config: self.deploy(config, VLLM_ONLY="1")["render.yaml"] for config in SWAPS}
        for config, render in renders.items():
            with self.subTest(config=config):
                self.assertIn(f'model-config: "{config}"', render)
                self.assertIn("type: Recreate", render)
                self.assertIn("- self-hosted", render)  # the fixed name the gateway's chat-default calls
                if config == "qwen3-8b":
                    self.assertIn("enable_thinking", render)
                else:
                    self.assertNotIn("enable_thinking", render)  # nothing carried over from Qwen3-8B
                    self.assertNotIn("default-chat-template-kwargs", render)


if __name__ == "__main__":
    unittest.main()
