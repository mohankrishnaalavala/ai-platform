"""preflight.sh with stand-ins for gcloud and gh (no cloud), in a scratch git repo that holds the real script, the
real Makefile (for `make status`), the real model configs and a stand-in golden set. One all-good run, then one
run per check with only that check's input broken: that check, and only it, says MISSING.
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
V1 = "projects/04-eval-gated-releases/evals/datasets/v1"
DATASET_TEST = "projects/04-eval-gated-releases/tests/test_golden_set.py"
GATEWAY_VALUES = "projects/05-agent-action-gateway/helm/action-gateway/values.yaml"
COUNTS = {"qa": 40, "structured": 20, "instruction": 20, "safety": 20}
PASSING_TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test(self):\n        pass\n"
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
CHECKS = 12  # project, 2 Slack secrets, 4 weights, the vLLM image, the action gateway image, golden set, variable, status

# Each knob breaks one input. STUB_NO_VERSION: a secret with no ENABLED version; STUB_PARTIAL: a weights folder
# missing its last shard; STUB_NO_IMAGE: an image not in Artifact Registry; STUB_RUNNING: a cluster is up.
GCLOUD = r"""#!/bin/bash
case "$1 $2" in
  "config get") echo proj ;;
  "projects describe") echo "${STUB_LABEL-ai-platform}" ;;
  "secrets versions") [ "$4" = "${STUB_NO_VERSION:-}" ] || echo "projects/1/secrets/$4/versions/1" ;;
  "storage ls")
    d=${3%/\*\*}; n=2; [[ -n "${STUB_PARTIAL:-}" && "$d" == *"$STUB_PARTIAL"* ]] && n=1
    printf '%s\n' "$d/config.json" "$d/tokenizer_config.json"
    for i in $(seq $n); do echo "$d/model-0000$i-of-00002.safetensors"; done ;;
  "artifacts docker") [[ -n "${STUB_NO_IMAGE:-}" && "$5" == *"$STUB_NO_IMAGE"* ]] && exit 1; echo sha256:abc ;;
  "container clusters") [ -z "${STUB_RUNNING:-}" ] || echo ai-platform ;;
esac
"""
GH = r"""#!/bin/bash
[ "$1 $2" = "variable list" ] || exit 1
h=${STUB_HOURS-72}
if [ -n "$h" ]; then echo "[{\"name\":\"COST_GUARD_MAX_AGE_HOURS\",\"value\":\"$h\"}]"; else echo "[]"; fi
"""


def dataset(counts):
    return {f"{V1}/{cat}.jsonl": "".join(json.dumps({"id": f"{cat}-{i:03d}", "category": cat}) + "\n"
                                          for i in range(1, n + 1)) for cat, n in counts.items()}


def commit(repo, files):
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "test")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=dict(os.environ, **GIT_ENV), capture_output=True)


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin, self.repo = self.tmp / "bin", self.tmp / "repo"
        self.bin.mkdir()
        for name, body in {"gcloud": GCLOUD, "gh": GH}.items():
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "remote", "add", "origin", ".")  # git fetch origin main: this repo's own main
        files = {p: (ROOT / p).read_text() for p in ["scripts/preflight.sh", "Makefile"]}
        files.update({f"config/models/{c.name}": c.read_text() for c in (ROOT / "config/models").glob("*.yaml")})
        commit(self.repo, {**files, **dataset(COUNTS), DATASET_TEST: PASSING_TEST})

    def preflight(self, **env):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", HOME=str(self.tmp), **GIT_ENV, **env)
        env.pop("MAKEFLAGS", None)
        run = subprocess.run(["bash", str(self.repo / "scripts/preflight.sh")], env=env, capture_output=True,
                             text=True, timeout=120, check=False)
        return run.returncode, run.stdout.splitlines()

    def test_all_good(self):
        rc, lines = self.preflight()
        self.assertEqual(rc, 0, "\n".join(lines))
        self.assertEqual(sum(x.startswith("OK ") for x in lines), CHECKS, "\n".join(lines))
        self.assertFalse([x for x in lines if x.startswith("MISSING")])

    def test_each_check_says_missing_alone(self):
        def edit_dataset(repo):  # a local change: no longer the golden set on main
            path = repo / V1 / "qa.jsonl"
            path.write_text(path.read_text().replace('"qa-001"', '"qa-999"'))

        cases = [  # (name, environment, change to the repo, the start of the line that must say MISSING)
            ("project label", {"STUB_LABEL": "other"}, None, "gcloud project"),
            ("slack-app-token", {"STUB_NO_VERSION": "slack-app-token"}, None, "Secret Manager secret slack-app-token"),
            ("slack-bot-token", {"STUB_NO_VERSION": "slack-bot-token"}, None, "Secret Manager secret slack-bot-token"),
            ("weights shard missing", {"STUB_PARTIAL": "Qwen3-8B"}, None, "weights for config/models/qwen3-8b.yaml"),
            ("vLLM image", {"STUB_NO_IMAGE": "vllm-openai"}, None, "vLLM image"),
            ("action gateway image", {"STUB_NO_IMAGE": "action-gateway:main"}, None, "action gateway image"),
            ("pinned action gateway digest", {"STUB_NO_IMAGE": "action-gateway@sha256:" + "b" * 64},
             lambda r: commit(r, {GATEWAY_VALUES: "image:\n  ref: r/action-gateway@sha256:" + "b" * 64 + "\n"}),
             "action gateway image action-gateway@sha256:bbbb"),
            ("golden set count", {}, lambda r: commit(r, dataset({**COUNTS, "qa": 39})), "golden set v1"),
            ("golden set changed locally", {}, edit_dataset, "golden set v1"),
            ("golden set test fails", {}, lambda r: commit(r, {DATASET_TEST: PASSING_TEST.replace("pass", "self.fail()")}),
             "golden set v1"),
            ("cost guard unset", {"STUB_HOURS": ""}, None, "repo variable COST_GUARD_MAX_AGE_HOURS"),
            ("cost guard at its default", {"STUB_HOURS": "8"}, None, "repo variable COST_GUARD_MAX_AGE_HOURS"),
            ("cluster running", {"STUB_RUNNING": "1"}, None, "make status"),
        ]
        for name, env, change, line in cases:
            with self.subTest(name):
                self.setUp()
                if change:
                    change(self.repo)
                rc, lines = self.preflight(**env)
                missing = [x for x in lines if x.startswith("MISSING")]
                self.assertEqual(rc, 1, "\n".join(lines))
                self.assertTrue([x for x in missing if x[len("MISSING  "):].startswith(line)], "\n".join(lines))
                # A wrong project also fails make status, which checks the project first.
                self.assertEqual(len(missing), 2 if name == "project label" else 1, "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
