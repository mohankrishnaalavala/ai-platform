# P5 end-to-end demo: the audit records, 2026-10-23

Claude Code, as the agent, called the action gateway's MCP (Model Context Protocol) tools. It scaled the `checkout` Deployment in `prod` from 2 to 4 replicas, then undid it. Each call needed a human approval in Slack, and each left two audit records: an intent record written after the approval and before the change, and a result record after it.

- **Where:** the session cluster on GKE (Google Kubernetes Engine); the records' Deployment snapshots carry the session label `20261023t1438z`. Policy version 1.
- **How the agent connected:** Claude Code on the laptop, through a `kubectl port-forward` to the gateway's MCP endpoint, with an agent token mapped to the team `payments` (the records' `team`).
- **Approver:** the Slack approver in Socket Mode, in the gateway's own process.
- **Raw records:** the four JSON files in this folder, copied from the audit bucket on 2026-10-23, the day they were written. The bucket deletes records after 30 days.
- **Changed before saving, and nothing else:** the Slack user ID is replaced by `<slack-user-id>` and the Slack user name by `<slack-user-name>`.

## The two actions

| | Scale up | Undo |
|---|---|---|
| Action ID | `20261023T182647Z-cf636ddd` | `20261023T182834Z-66389e54` (`undo_of`: the scale-up) |
| Tool call | `scale_deployment` prod/checkout, replicas 4 | `undo_action` with the scale-up's action ID |
| Dry run | OK: 2 pods affected, capacity change +100%, no PDB violation | OK: 2 pods affected, capacity change −50%, no PDB violation |
| Decision | `needs_approval`: "prod is production: every write needs a human approval" | `needs_approval`: the same reason |
| Approval | approved in Slack after 51.119 s | approved in Slack after 43.503 s |
| Replicas before → after | 2 → 4 | 4 → 2 |
| Result | executed | executed |

The approval wait is the record's `approver.waited_s`: from the moment the gateway asked the Slack approver (posting the request) until the answer came back (`src/actions.py`). It is mostly a human's reaction time, not gateway latency.

## The AI risk summaries (from the records, unchanged)

The LLM (large language model) explains; the policy decides. The chart sends the summary request to the P2 gateway alias `chat-hosted` (`risk.model` in `helm/action-gateway/values.yaml`); the records don't name the model that answered.

Scale up:

> Scaling the payments checkout deployment in prod from 2 to 4 replicas.
> Traffic spikes or upstream payment dependencies could struggle with the doubled capacity.
> Check that the payments team is ready for the increased traffic and no PDB violations exist.

Undo:

> Scale payment checkout deployment down from 4 to 2 replicas, reducing production capacity by 50 percent.
> Two active pods will terminate, which could briefly increase latency or error rates if traffic spikes.
> Verify that current traffic volume is low enough to handle the reduced capacity safely.

Opinion: the first summary's second line is a weak reason, since `checkout` runs a pause container here. That's why the summary only advises the human and never decides.
