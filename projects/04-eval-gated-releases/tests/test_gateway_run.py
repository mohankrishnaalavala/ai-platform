"""P4-05 in CI: run.py against P2's gateway in containers (ci.yml, job gateway), with the mock provider in place of
vLLM (chat-default) and Gemini (chat-hosted). It proves the plumbing against the real LiteLLM: the model that
answered and the fallback header on every call, the tokens, the cost, both results files and the config hash.

The mock answers "reply from <name>" to everything, so the scores mean nothing, and the judge (the `judge` alias,
which CI points at the mock in tests/gateway-ci.yaml) never gives a score. So every run here is INVALID, for that
reason at least.
With VLLM_DOWN=1 (after the mock vLLM is stopped), every answer comes from the fallback, which makes it INVALID.
Skipped unless GATEWAY_URL and MASTER_KEY are set.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "evals"))
import run

DOWN = os.environ.get("VLLM_DOWN") == "1"
CONFIG = run.ROOT / "config/models/qwen2.5-7b-bf16.yaml"  # self-hosted, so the cost includes GPU minutes
VLLM, HOSTED = "openai/qwen", "openai/hosted"  # the aliases' models in P2's helm/gateway/values-ci.yaml
JUDGE = "openai/judge"  # the judge alias's model in CI (tests/gateway-ci.yaml)


@unittest.skipUnless(os.environ.get("GATEWAY_URL") and os.environ.get("MASTER_KEY"),
                     "needs the CI gateway (GATEWAY_URL, MASTER_KEY)")
class RunnerAgainstTheGateway(unittest.TestCase):
    def test_run(self):
        rounds = 1 if DOWN else 2
        with tempfile.TemporaryDirectory() as out:
            p = subprocess.run([sys.executable, str(HERE.parent / "evals" / "run.py"), "--target", "chat-default",
                                "--config", str(CONFIG), "--dataset", str(HERE / "fixtures" / "dataset"),
                                "--judge", "judge", "--repeat", str(rounds), "--out", out],
                               capture_output=True, text=True, timeout=600, check=False)
            print(p.stderr, p.stdout)
            self.assertEqual(p.returncode, 1)  # INVALID, as the docstring says
            [path] = Path(out).glob("*/*.json")
            self.assertTrue(Path(str(path)[:-len(".json")] + ".md").exists())
            r = json.loads(path.read_text())
        self.assertEqual(r["config_hash"], path.parent.name)
        self.assertEqual(r["config_hash"], run.config_hash(run.hash_parts(r["config"], r["dataset"], r["judge"])))
        self.assertEqual(len(r["cases"]), 10 * rounds)
        for c in r["cases"]:
            self.assertEqual(c["status"], 200, c["id"])
            self.assertEqual((c["model"], c["fallbacks"]), (HOSTED, "1") if DOWN else (VLLM, "0"), c["id"])
            self.assertEqual(c["tokens"], {"prompt": 50, "completion": 10}, c["id"])  # the mock's fixed usage
            if c["kind"] == "judge":
                self.assertEqual((c["judge"]["status"], c["judge"]["model"], c["judge"]["fallbacks"]), (200, JUDGE, "0"))
        expected = "answered by a fallback" if DOWN else "the judge gave no score"
        judged = sum(c["kind"] == "judge" for c in r["cases"])
        self.assertEqual(len(r["invalid_reasons"]), len(r["cases"]) if DOWN else judged)
        self.assertTrue(all(expected in x for x in r["invalid_reasons"]), r["invalid_reasons"])
        self.assertGreater(r["cost"]["gpu"]["minutes"], 0)
        self.assertEqual(r["cost"]["unpriced_models"], [JUDGE])  # the judge's model has no price: cost [X]


if __name__ == "__main__":
    unittest.main()
