"""Unit tests for config_hash.py and the model configs. Run: python3 -m unittest discover -s scripts"""
import hashlib
import os
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


if __name__ == "__main__":
    unittest.main()
