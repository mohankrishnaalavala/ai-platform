# Gateway behaviour in CI, with mock providers (2026-10-18)

**What:** `ci.yml`, job `gateway`. The gateway runs in containers (`tests/compose.yaml`) with the chart's own LiteLLM config and start script, rendered from `helm/gateway` with `values-ci.yaml`. Mock providers stand in for vLLM and the hosted model and record every request body.

**Hardware:** GitHub-hosted `ubuntu-24.04` runner. No GPU, no cloud.

**Versions** (all pinned by digest; see `docs/versions.md`):
- LiteLLM `v1.103.3`, open-source features, no license key
- Presidio `2.2.362`
- Postgres `17.6`
- OTel Collector contrib `0.161.0`

The lines below are copied from the job logs, which GitHub keeps for 90 days. These are the gateway's own behaviours. The real vLLM and the real hosted model are measured in the cloud sessions.

## Teams, limits and keys (run [39152332898](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39152332898), PR #33)
```text
budget $1e-05: status codes [200, 200, 429]
rpm 2: status codes [200, 200, 429, 429]
test_a_revoked_key_is_rejected ... ok          (200 before /key/delete, 401 after)
```
A `chat-default` call to the mock costs $0.0000075: 10 output tokens at the internal price of $0.00000075 per output token. So a $0.00001 team budget allows 2 calls.

## Fallback when the self-hosted model dies in use
The mock vLLM is stopped after a call to it, so the gateway's open connection to it hangs. `chat-default` has `timeout` 30 s and `stream_timeout` 10 s.
```text
run 39151618082 (PR #32):  fallback (plain): status 200, 30747 ms, attempted fallbacks 1
                           fallback (stream): status 200, 11007 ms, attempted fallbacks 1
run 39152332898 (PR #33):  fallback (plain): status 200, 30139 ms, attempted fallbacks 1
                           fallback (stream): status 200, 11008 ms, attempted fallbacks 1
```
With no earlier call, the stopped mock's host name simply isn't found. In that case fallbacks took 40 ms (plain) and 45 ms (streaming) (run 39151304502).

## PII masking (run [39158975902](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39158975902), PR #34)
- **Prompts:** the 20 prompts in `tests/pii_prompts.json`, with fake values: 10 emails, 10 phones and 8 card numbers in varied formats.
- **Masking:** `mask-pii` guardrail with `default_on: true` and 7 entity types.

```text
direct (plain and streaming): raw values that got through, per type: card 0/16, email 0/20, phone 0/20
fallback (chat-default to the hosted alias): raw values that got through, per type: card 0/8, email 0/10, phone 0/10
spend-log rows: 34
logs, traces and spend logs: raw values that got through, per type: card 0/8, email 0/10, phone 0/10
fail closed (pii-fail-closed-plain): status 500, requests at the provider 0
fail closed (pii-fail-closed-stream): status 500, requests at the provider 0
```

**What it covers and what it doesn't:**
- **Checked:**
  - That no raw value reached the provider, the gateway's logs, the collector's full trace output or the spend logs.
  - That each request still reached the provider with its tag, so the whole prompt was not simply replaced.
- **Not checked:** whether masking was precise, meaning other words replaced too (over-masking).
- **Fail-closed:** with the analyzer stopped, the gateway refused the request with HTTP 500, and nothing was sent.

## A Presidio start-up hang (run [39159923845](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39159923845), PR #34)
The next run of the same job failed before any test ran. The anonymizer's gunicorn master started, but its worker never booted: there is no `Booting worker` line, while the analyzer booted its worker in the same second. The container stayed unhealthy for about 8.5 minutes, until Compose gave up. Nothing in the gateway had changed: that commit only added this file.
```text
presidio-anonymizer-1  | [2026-10-18 19:42:49 +0000] [1] [INFO] Listening at: http://0.0.0.0:3000 (1)
presidio-anonymizer-1  | [2026-10-18 19:42:49 +0000] [1] [INFO] Control socket listening at /app/gunicorn.ctl
 Container tests-presidio-anonymizer-1  Error
dependency failed to start: container tests-presidio-anonymizer-1 is unhealthy
```
**Response:**
- **In the chart:** Presidio's pods now have a liveness probe, and the anonymizer's startup limit is 2 minutes (the analyzer keeps 10). Kubernetes restarts a hung pod instead of leaving the gateway refusing every request.
- **In CI:** an unhealthy Presidio container is restarted once, with a visible warning.

## 2026-10-19: requests-only masking, and streaming (run [39190443042](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39190443042), PR #40)
**What changed:** every result above ran with LiteLLM's default Presidio scope, "both": requests and answers were masked. Session 3 found that the pass on answers holds a stream back until the answer is done (`results/session3-gateway-full-2026-10-19.md`). The chart now sets `presidio_filter_scope: input`, so requests are masked and answers aren't. The mock vLLM now sends its streamed events 0.3 s apart, and `test_streaming.py` checks that they arrive that way.

The same checks, with requests-only masking:
```text
stream: 3 pieces of content, first at 321 ms, last at 921 ms
direct (plain and streaming): raw values that got through, per type: card 0/16, email 0/20, phone 0/20
fallback (chat-default to the hosted alias): raw values that got through, per type: card 0/8, email 0/10, phone 0/10
spend-log rows: 35
logs, traces and spend logs: raw values that got through, per type: card 0/8, email 0/10, phone 0/10
fail closed (pii-fail-closed-plain): status 500, requests at the provider 0
fail closed (pii-fail-closed-stream): status 500, requests at the provider 0
budget $1e-05: status codes [200, 200, 429]
rpm 2: status codes [200, 200, 429, 429]
fallback (plain): status 200, 30822 ms, attempted fallbacks 1
fallback (stream): status 200, 10999 ms, attempted fallbacks 1
```
The streaming fallback (11.0 s here, 0.7 s in sessions 2 and 3) depends on how the backend failed: CI's stopped mock likely leaves a hung connection that waits out the 10 s `stream_timeout`, while in the sessions vLLM's Service, with no pods, likely refused at once. Neither cause was measured directly.

**Before the fix**, the streaming test failed in runs [39189519299](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39189519299) and [39189642556](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39189642556): `AssertionError: 1 not greater than or equal to 2` (one piece of content).

**What requests-only masking leaves out:** an answer that contains personal data, because the model repeated or made it up, reaches the caller unmasked. Nothing raw reaches a provider: requests are masked before they leave.
