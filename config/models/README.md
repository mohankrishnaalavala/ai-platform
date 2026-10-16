# config/models/

One YAML file per model configuration. Each file is also a Helm values file for the vLLM chart.

The **config hash** is the first 16 hex characters of the file's sha256 (`scripts/config_hash.py <file>`). It names every results folder, and the P4 eval gate checks it.

- `qwen2.5-7b-bf16.yaml`: Qwen2.5-7B-Instruct with its published 16-bit (BF16) weights.
- `qwen2.5-7b-fp8.yaml`: the same model, which vLLM compresses to 8-bit (FP8) while loading. It differs from the BF16 file only in `name` and `quantization`; a unit test checks this.
