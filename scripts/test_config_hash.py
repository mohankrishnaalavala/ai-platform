"""Unit tests for config_hash.py and the model configs. Run: python3 -m unittest discover -s scripts"""
import hashlib
import os
import re
import tempfile
import unittest

from config_hash import config_hash

MODELS = os.path.join(os.path.dirname(__file__), "..", "config", "models")


class ConfigHashTest(unittest.TestCase):
    def test_hash_is_sha256_prefix_and_changes_with_content(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".yaml", delete=False) as f:
            f.write(b"a: 1\n")
        try:
            self.assertEqual(config_hash(f.name), hashlib.sha256(b"a: 1\n").hexdigest()[:16])
            with open(f.name, "ab") as g:
                g.write(b" ")
            self.assertNotEqual(config_hash(f.name), hashlib.sha256(b"a: 1\n").hexdigest()[:16])
        finally:
            os.unlink(f.name)

    def test_bf16_and_fp8_differ_only_in_name_and_quantization(self):
        # Otherwise the FP8 numbers wouldn't be comparable with the BF16 ones.
        with open(os.path.join(MODELS, "qwen2.5-7b-bf16.yaml")) as f:
            bf16 = f.read().splitlines()
        with open(os.path.join(MODELS, "qwen2.5-7b-fp8.yaml")) as f:
            fp8 = f.read().splitlines()
        self.assertEqual(len(bf16), len(fp8))
        diff = [(a, b) for a, b in zip(bf16, fp8) if a != b]
        self.assertEqual([a.split(":")[0].strip() for a, _ in diff], ["name", "quantization"])
        self.assertIn("quantization: fp8", diff[1][1])

    def test_every_config_pins_image_and_model(self):
        # mirror.yml copies exactly these pins, and vLLM reads the weights from bucketPath.
        configs = sorted(f for f in os.listdir(MODELS) if f.endswith(".yaml"))
        self.assertGreaterEqual(len(configs), 4)
        for name in configs:
            with self.subTest(config=name), open(os.path.join(MODELS, name)) as f:
                v = dict(re.findall(r"^ *(name|hfId|revision|bucketPath|digest): (\S+)", f.read(), re.MULTILINE))
                self.assertEqual(v["name"] + ".yaml", name)
                self.assertRegex(v["digest"], r"^sha256:[0-9a-f]{64}$")
                self.assertRegex(v["revision"], r"^[0-9a-f]{40}$")
                self.assertEqual(v["bucketPath"], v["hfId"].split("/")[1] + "/" + v["revision"])


if __name__ == "__main__":
    unittest.main()
