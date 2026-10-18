# P2 session 1: gateway shakedown on GKE, no GPU (2026-10-18)

**Run:** GitHub Actions [39163029447](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39163029447), `gpu-session.yml` mode `gateway-shakedown`, zone `us-central1-c`, from `main` at commit `de38a7f`.

**Hardware:**
- GKE `v1.36.4-gke.1495002`, one `e2-standard-4` system node (4 vCPU, 16 GB)
- The GPU pool stayed at 0 nodes.

**Software:**
- LiteLLM `v1.103.3` (open-source features, no license key)
- Postgres `17.6`
- Presidio `2.2.362`
- OTel Collector chart `0.174.2`
- the test-only mock provider (alias `chat-hosted-mock`)
- `chat-hosted` is `gemini-3.5-flash-lite` on Vertex AI, global endpoint
- There was no vLLM in this session, so `chat-default` had no backend.

**Method:** `make up LAYERS=02` (with `MOCK_PROVIDER=1`), then `make test P=02` (`tests/run.sh`). These are the same test files as CI, run against the cluster through port-forwards. The lines below are copied from the run's log, which GitHub keeps for 90 days.

## Results
```text
test_the_hosted_alias_answers ... ok        (chat-hosted: Gemini on Vertex AI, keyless, from the gateway pod)
test_the_default_alias_answers ... ok       (no vLLM: answered through the fallback)
budget $1e-05: status codes [200, 200, 200, 200, 429]
rpm 2: status codes [200, 200, 429, 429]
direct (plain and streaming): raw values that got through, per type: card 0/16, email 0/20, phone 0/20
fallback (plain): status 200, 586 ms, attempted fallbacks 1
fallback (stream): status 200, 785 ms, attempted fallbacks 1
team shakedown-204403 (<team id>): budget $1, 10 requests a minute; onboarded in 0.6 s
PASS: make team (end to end 3.9 s)
dump: 1005 lines, 37 spend-log rows
logs, traces and spend logs: raw values that got through, per type: card 0/8, email 0/10, phone 0/10
fail closed (pii-fail-closed-plain): status 500, requests at the provider 0
fail closed (pii-fail-closed-stream): status 500, requests at the provider 0
all P2 checks passed
```

**Notes:**
- **Secrets and the keyless call:** the gateway started with its master key and Postgres password mounted from Secret Manager by the GKE add-on. Its pod called Gemini on Vertex AI with only the custom `vertexPredict` role (`aiplatform.endpoints.predict`), through Workload Identity, with no API key anywhere.
- **Budget:** a `chat-default` call here falls back to Gemini, priced from LiteLLM's built-in price list. The $0.00001 budget allowed 4 calls, where CI's mock allowed 2.
- **Fallback timing:** 586 ms and 785 ms. With no vLLM Service at all, `chat-default` fails at once (the host isn't found), so this is the fast case. A vLLM that dies in use costs about the timeout (`results/ci-gateway-2026-10-18.md`).
- **`make team` timing:** 3.9 s end to end, including the port-forward and reading the master key. The script's own time was 0.6 s.
- **What the PII scan read:** the LiteLLM, collector and Presidio logs, plus the spend logs. Container logs also go to Cloud Logging.
- **Fail-closed:** the analyzer was scaled to 0, then put back.

## Resource use (`kubectl top`, at the end of the tests)
| Pod | CPU | Memory |
|---|---|---|
| litellm | 7m | 573Mi |
| postgres | 28m | 79Mi |
| otel-collector | 2m | 64Mi |
| presidio-anonymizer | 1m | 43Mi |
| mock-provider | 1m | 17Mi |
| presidio-analyzer | [X] (just restarted after the fail-closed test; no reading yet) | [X] |

**Node:** requests were 2221m CPU (56%) and 5.73 GB memory (41%) of the `e2-standard-4`; limits were 98% of memory. P1's Prometheus and KEDA weren't in this session, so the GPU session has to check the headroom again.

## Timing and teardown
- **Timing:** `make up` started at 20:23:46Z, and the tests began at 20:43Z. The P2 checks took about 2 minutes (20:43:24Z to 20:45:01Z).
- **Teardown:** the teardown job's `make status` at 20:56:25Z reported "nothing billable is running".
