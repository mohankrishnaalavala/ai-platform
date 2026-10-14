# local/ — laptop mode

Free, offline-friendly copy of the platform for daily development and CI. Built in [P0](../projects/00-foundation/).

- **Kubernetes:** kind
- **Model:** Ollama serving a tiny model (e.g. `qwen2.5:0.5b`) on CPU, behind the same OpenAI-compatible API as vLLM
- **Hosted provider:** a mock server that records payloads (P2 uses it to prove PII masking)
- **Same Helm charts** as cloud mode, with `values-local.yaml`

```bash
make local-up LAYERS=1,2     # bring up the layers built so far
make test P=02
make local-down
```

Target: a fresh clone reaches a working smoke test in ≤10 minutes.
