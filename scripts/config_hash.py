#!/usr/bin/env python3
"""Print a model config's hash: the first 16 hex characters of the file's sha256.

Every results folder is named after it, so each number traces back to an exact config.
Usage: scripts/config_hash.py config/models/qwen2.5-7b-bf16.yaml
"""
import hashlib
import sys


def config_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(config_hash(sys.argv[1]))
