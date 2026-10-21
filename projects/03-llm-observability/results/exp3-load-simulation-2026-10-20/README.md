# Experiment 3 with closed-loop and open-loop load: a promtool simulation (2026-10-20)

**Why:** before the session 2 retry, a check of the game day asked whether experiment 3 (hosted model blocked while vLLM is down) could fire the availability alert in its 10 minutes. Each failing request waits out `chat-hosted`'s 60 s timeout. A closed-loop load, which sends the next request only after the last one answers, would then send about 1 request a minute instead of 30.

**Method:** [`exp3-test.yaml`](exp3-test.yaml) feeds the real SLO rules (`slos/platform.rules.yaml`, Sloth's output) a made-up traffic shape modeled on the first session 2 run. It's a guess at the shape, not a measurement.
- The good requests a minute before experiment 3:
  - 30 for 6 min (screenshots), then 0 for 2 min, then 30 for 2 min (the game day's warm-up)
  - 17 for 10 min, then 2 for 20 min (the flood)
  - 30 for 25 min (recovery, the wait for the fallback alert to clear, experiment 1)
- **Experiment 3** starts at minute 65 (`1h5m`) with no successes, and with either:
  1. 1 error a minute (closed-loop), or
  2. 30 errors a minute (open-loop).

Each test expects no alert at each minute from 65 to 77, so promtool prints every minute an alert does fire ([`promtool-output.txt`](promtool-output.txt), `prom/prometheus:v3.15.0`, `promtool test rules exp3-test.yaml`).

**Result** (1-minute rule evaluation; the cluster adds scrape, flush and Alertmanager waits):

| Load | Ticket | Page |
|---|---|---|
| Closed-loop, 1 error a minute | from `1h11m`: 6 min into the 10-min block | never, through minute 77 |
| Open-loop, 30 errors a minute | `1h6m`: 1 min in | from `1h7m`: 2 min in |

The game day's load became open-loop before the retry (PR #60). In the retry, the real alert reached the receiver 2.3 min (ticket) and 3.8 min (page) after the block ([`session2-retry-obs-full-2026-10-21.md`](../session2-retry-obs-full-2026-10-21.md)).
