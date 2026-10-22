# config/models/

One YAML file per model configuration. Each file is also a Helm values file for the vLLM chart.

The **config hash** is the first 16 hex characters of the file's sha256 (`scripts/config_hash.py <file>`). It names every results folder, and the P4 eval gate checks it.

- `qwen2.5-7b-bf16.yaml`: Qwen2.5-7B-Instruct with its published 16-bit (BF16) weights.
- `qwen2.5-7b-fp8.yaml`: the same model, which vLLM compresses to 8-bit (FP8) while loading. It differs from the BF16 file only in `name` and `quantization`; a unit test checks this.
- `qwen3-8b.yaml`: Qwen3-8B, P4's newer open model, with thinking mode off (`defaultChatTemplateKwargs`). Evaluated offline; not served by default.
- `qwen2.5-1.5b.yaml`: Qwen2.5-1.5B-Instruct, P4's deliberately bad candidate. Evaluated offline; not served by default.

A unit test checks that every file pins the image by digest and the model by commit, as `mirror.yml` needs.
