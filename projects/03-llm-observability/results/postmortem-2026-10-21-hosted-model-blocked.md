# Postmortem: chat-default down for 10 minutes, self-hosted and hosted model both unreachable (game day, 2026-10-21)

**Format:** blameless. This was an injected failure (game day experiment 3), so the "cause" is known. The point is how fast we noticed, what users saw, and what would make it better.

**Source:** session 2 retry, run [39519329832](https://github.com/mohankrishnaalavala/ai-platform/actions/runs/39519329832), and its raw files in [`session2-retry-20261021t0007z/gameday/`](session2-retry-20261021t0007z/gameday/): `load.txt` (every background request), `alerts.jsonl` (every alert the receiver got), `events.jsonl` (experiment start times). The full run is in [`session2-retry-obs-full-2026-10-21.md`](session2-retry-obs-full-2026-10-21.md).

## Summary

The gateway's `chat-default` route failed every request for about 10 minutes, and each failure took about 60 s to arrive.
- The self-hosted model (vLLM) had been down for 15 minutes before this. The gateway was covering for it by falling back to the hosted model.
- Then the hosted model became unreachable too (a NetworkPolicy blocked the gateway's calls out of the cluster), so the fallback failed as well.
- The availability SLO's alert opened a ticket 2.3 minutes after the block, and paged 3.8 minutes after it.

## Impact
- **Requests:** the background load sends one `chat-default` request every 2 s. Every request that started between about 01:55:46Z and 02:05:07Z failed: 281 returned 500, 270 of them ending inside the block and 11 just after it.
- **Wait per failure:** each failure took the hosted model's 60 s timeout: median 60,744 ms during the block.
- **Before it:** for the 15 minutes before (vLLM down, hosted model up), 451 of 451 requests returned 200 (450 through the fallback), median 577 ms. Users weren't affected then, but the self-hosted model was gone and only the fallback alert said so.
- **Data:** none lost. The gateway keeps no request data, and no prompt text is in the traces.

## Timeline (UTC, 2026-10-21)

| Time | What happened | Source |
|---|---|---|
| 01:40:40 | vLLM killed and held at 0 replicas (experiment 1). Requests start falling back to the hosted model. | `events.jsonl` |
| 01:52:11 | `ChatDefaultFallbackHigh` reaches the receiver, 11.5 min after the kill (its rule waits 10 min by design). | `alerts.jsonl` |
| 01:55:43 | The NetworkPolicy blocks the gateway's calls out of the cluster (experiment 3). vLLM is still down. | `events.jsonl` |
| 01:56:47 | The first failure comes back: 500 after 60,298 ms. | `load.txt` |
| 01:58:02 | `GatewayAvailabilityBudgetBurn` ticket reaches the receiver: **2.3 min** after the block, about 75 s after the first failure came back. | `alerts.jsonl` |
| 01:59:32 | `GatewayAvailabilityBudgetBurn` page: **3.8 min** after the block. | `alerts.jsonl` |
| about 02:05:47 | The block is removed and vLLM is allowed back (KEDA unpaused). | [results file](session2-retry-obs-full-2026-10-21.md): vLLM Ready 476 s later |
| 02:06:08 | The last failure comes back. From 02:06:10 requests succeed again through the hosted model. | `load.txt` |
| 02:13:43 | vLLM serves again, 476 s after it was allowed back. No GPU node was left, so that includes starting one. | `recovery.txt` |
| 02:13:45 | The game day ends. The receiver had no "resolved" notice for the availability alert yet: its 1-hour and 6-hour windows keep it on after the errors stop. | `alerts.jsonl` |

## Detection
- **What worked:** the availability alert's ticket arrived 75 s after the first failed response, and its page 165 s after. Each failure takes 60 s to come back, so the first errors reach the metrics a minute late; the ticket followed 75 s after that.
- **What almost didn't work:** before the run, the background load was "closed-loop": it sent the next request only after the last one answered. With every request now taking 60 s, it would have sent about one request a minute instead of 30.
  - A simulation with the real alert rules, on a made-up traffic shape modeled on the first session 2 run, fired only the ticket, 6 min into the 10-min block, and never the page ([`exp3-load-simulation-2026-10-20/`](exp3-load-simulation-2026-10-20/)).
  - Real clients behave like the fixed-rate load: they don't wait for each other. A test load that slows down with the system hides the outage it is meant to show. This is called coordinated omission.
  - Fixed before the run (PR #60): one request every 2 s on its own thread.
- **The earlier, quieter failure:** vLLM was down for 15 minutes before any user saw an error. Only the fallback alert, 11.5 minutes in, said so. Without it, the first sign would have been this outage.

## Contributing factors
1. **One fallback.** `chat-default` has a single fallback, one hosted provider. With both down, there's nothing left.
2. **A fallback hides the first failure.** While the hosted model answered, users were fine, so the availability SLO saw nothing wrong. Only the fallback alert tracks the self-hosted model's health.
3. **Long timeout on the last hop.** `chat-hosted` waits 60 s for a plain request (10 s for streaming). When the hosted model is unreachable, every request hangs for a minute before it fails. That slows the user's error and the alert.

## What went well
- The availability alert fired fast, both ticket and page, with the SLO's real 28-day burn-rate windows.
- Recovery needed no human steps: removing the block brought success back within 23 s (through the hosted model), and KEDA brought vLLM back in 476 s.
- Every time and count above comes from saved raw files, not memory.

## Action items (proposed, not built: outside P3's scope)

| Action | Why | Type |
|---|---|---|
| A second hosted fallback (another provider or region) for `chat-default` | Removes the single point of failure in factor 1 | Prevent |
| A shorter plain-request timeout on `chat-hosted`, or fail fast when the provider can't be reached | Cuts the 60 s hang per failed request (factor 3) and brings the alert a minute closer | Mitigate |
| Treat `ChatDefaultFallbackHigh` as urgent when it lasts | vLLM was down 15 min with users unaware; this outage was one more failure away | Detect |
| Keep fixed-rate (open-loop) load for every alert test | A closed loop hid most of this outage's errors in simulation | Test |
