# Onboarding a team to the LLM gateway

The gateway is the one front door to every model the platform offers. A team gets its own key, a
budget and a rate limit, instead of a provider's API key of its own.

## What the platform team runs

```bash
make team NAME=payments BUDGET_USD=20 RPM=60          # optional: TPM=100000 (tokens a minute)
```

It prints, once:

```text
team payments (<team id>): budget $20, 60 requests a minute; onboarded in <n> s
key (shown once; the gateway keeps only its hash): sk-...
```

Hand the key to the team lead through your secret store, not chat or email. It can't be shown again.
If it's lost, revoke it (below) and generate another for the same team with the gateway's
`/key/generate` API and the team id; `make team` itself only creates new teams.

- **NAME:** 3 to 40 lowercase letters, digits or dashes. One name, one team; `make team` refuses a name that exists.
- **BUDGET_USD:** the most the team can spend in total. Self-hosted calls count too, at the platform's own cost per token. Once it's spent, calls return HTTP 429 (budget exceeded).
- **RPM / TPM:** requests and tokens a minute, set on the team rather than on each key. Over the limit, calls return HTTP 429.

## What the team uses

The gateway speaks the OpenAI API, so any OpenAI client works. Point it at the gateway and use the
team key:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:4000/v1", api_key="<team key>")
reply = client.chat.completions.create(model="chat-default", messages=[{"role": "user", "content": "Hello"}])
```

| Model name | What answers |
|---|---|
| `chat-default` | The platform's own model (Qwen2.5-7B-Instruct on a GPU). If it's down or too slow, the gateway sends the request to `chat-hosted` instead. |
| `chat-hosted` | A hosted model on Google Cloud (Gemini on Vertex AI). |

**Personal data is masked first.** Before any request reaches a model, the gateway replaces email
addresses, phone numbers, card numbers, names, US social security numbers, IBANs and IP addresses
with placeholders such as `<EMAIL_ADDRESS>`. It does this for every request, including one that falls
back to the hosted model, and refuses the request if the detector is down. The tests cover emails,
phone numbers and card numbers (`tests/test_pii.py`). Detection isn't perfect: don't send personal data
you can avoid sending.

In this dev setup the gateway has no public address: reach it with
`kubectl port-forward -n gateway svc/gateway 4000:4000`.

## Revoking a key

```bash
curl -X POST http://127.0.0.1:4000/key/delete -H "Authorization: Bearer <master key>" \
  -H "Content-Type: application/json" -d '{"keys": ["<team key>"]}'
```

The key stops working at once (HTTP 401).

## Limits of the dev setup
- **Short-lived:** teams and keys live in a Postgres that lasts one session; `make down` removes them. A long-lived setup would use Cloud SQL.
- **One replica:** the gateway runs as one copy, because rate limits are counted in its memory. More copies would need Redis.
