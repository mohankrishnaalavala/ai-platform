# P5 end-to-end demo: the audit records, 2026-10-23

Claude Code, as the agent, called the action gateway's MCP (Model Context Protocol) tools. It scaled the `checkout` Deployment in `prod` from 2 to 4 replicas, then undid it. Each call needed a human approval in Slack, and each left two audit records: an intent record written after the approval and before the change, and a result record after it.

- **Where:** the session cluster on GKE (Google Kubernetes Engine), us-central1-c, session `20261023t1438z`. The gateway image is the one `mirror.yml` pushed from `main`, pinned by digest. Policy version 1.
- **How the agent connected:** Claude Code on the laptop, through a `kubectl port-forward` to the gateway's MCP endpoint, with a per-session agent token mapped to the team `payments`. The token was deleted after the session.
- **Approver:** the Slack approver in Socket Mode, in the gateway's own process.
- **Raw records:** the four JSON files in this folder, copied from the audit bucket on 2026-10-23, the day they were written. The bucket deletes records after 30 days.
- **Changed before saving, and nothing else:** the Slack user ID is replaced by `<slack-user-id>` and the Slack user name by `<slack-user-name>`.

## The two actions

| | Scale up | Undo |
|---|---|---|
| Action ID | `20261023T182647Z-cf636ddd` | `20261023T182834Z-66389e54` (`undo_of`: the scale-up) |
| Tool call | `scale_deployment` prod/checkout, replicas 4 | `undo_action` with the scale-up's action ID |
| Dry run | OK: 2 pods affected, capacity change +100% | (the undo restores the before snapshot) |
| Decision | `needs_approval`: "prod is production: every write needs a human approval" | `needs_approval`: the same reason |
| Approval | approved in Slack after 51.119 s | approved in Slack after 43.503 s |
| Replicas before → after | 2 → 4 | 4 → 2 |
| Result | executed | executed |

The approval wait is the record's `approver.waited_s`: from the moment the gateway asked the Slack approver (posting the request) until the answer came back (`src/actions.py`). It is mostly a human's reaction time, not gateway latency.

## The AI risk summaries (from the records, unchanged)

The LLM (large language model) explains; the policy decides. The summaries came from `chat-hosted` (Gemini on Vertex AI) through the P2 gateway.

Scale up:

> Scaling the payments checkout deployment in prod from 2 to 4 replicas.
> Traffic spikes or upstream payment dependencies could struggle with the doubled capacity.
> Check that the payments team is ready for the increased traffic and no PDB violations exist.

Undo:

> Scale payment checkout deployment down from 4 to 2 replicas, reducing production capacity by 50 percent.
> Two active pods will terminate, which could briefly increase latency or error rates if traffic spikes.
> Verify that current traffic volume is low enough to handle the reduced capacity safely.

The first summary's second line is a weak reason (more replicas don't strain upstream dependencies in this demo, where `checkout` runs a pause container). That's the point of the design: the summary is advice for the human, and it never decides.
