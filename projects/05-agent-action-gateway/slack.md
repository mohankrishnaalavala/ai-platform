# Slack approvals: what to set up

The action gateway asks a person in Slack before a risky change runs. It uses Slack's Socket Mode: the gateway opens a connection out to Slack, so Slack never calls into the cluster and there is no public endpoint. This takes about 20 minutes, once. kind and CI don't need any of it: they use the CLI (command line) approver.

**What a request looks like.** One message per request, with:
- the tool, the target and the requesting team
- the policy's decision and reasons
- the dry run (pods affected, capacity change, PodDisruptionBudget violations) and the diff
- apart from those, labelled, the "AI summary": an LLM (large language model) explains the facts; it doesn't decide
- Approve and Reject buttons

Only the Slack users you list can answer. A click from anyone else is refused, and the refusal is written to the audit log. With no answer in 15 minutes, the request is rejected.

## 1. In Slack

1. **A workspace and a channel**, for example `#ai-platform-approvals`.
2. **The app.** At [api.slack.com/apps](https://api.slack.com/apps): Create New App, then From a manifest. Pick the workspace and paste [`slack-app-manifest.yaml`](slack-app-manifest.yaml). It turns on Socket Mode and interactivity (the buttons), and asks for one bot scope: `chat:write`.
3. **The app-level token** (`xapp-...`): Basic Information, then App-Level Tokens, then Generate Token and Scopes. Add the scope `connections:write`, which Socket Mode needs. Copy the token.
4. **The bot token** (`xoxb-...`): OAuth & Permissions, then Install to Workspace, then Allow. Copy the Bot User OAuth Token.
5. **Invite the bot** to the channel: type `/invite @action-gateway` in it.
6. **Write down two kinds of IDs.** They aren't secrets, but keep them out of Git anyway.
   - The channel ID (`C...`): open the channel's details; the ID is at the bottom.
   - The user ID (`U...`) of each person who may approve: open their profile, then More (the three dots), then Copy member ID. Make two lists: everyday approvers, and the break-glass approvers. Only the break-glass list can answer a break-glass request (a change during the prod freeze).

## 2. The tokens into Secret Manager

The tokens never go into chat, Git, a values file or a log. Check the project first, then paste each token at the prompt. `read -s` keeps it off the screen and out of your shell history.

```bash
export CLOUDSDK_ACTIVE_CONFIG_NAME=ai-platform CLOUDSDK_CORE_DISABLE_PROMPTS=1
gcloud config get project
read -rs t && printf '%s' "$t" | gcloud secrets versions add slack-app-token --data-file=- && unset t
read -rs t && printf '%s' "$t" | gcloud secrets versions add slack-bot-token --data-file=- && unset t
```

The secrets themselves already exist (the foundation made them). Only the action gateway's Kubernetes service account can read them.

## 3. Test it from the laptop (no cluster, no cost)

Put the tokens and IDs in a `.env` file at the repo root. Git ignores `.env`.

```bash
SLACK_APP_TOKEN=xapp-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=C...
SLACK_APPROVERS=U...,U...
```

Then run the approver once, with a Python that has the gateway's pinned packages:

```bash
python3.13 -m venv .venv
.venv/bin/pip install --require-hashes -r projects/05-agent-action-gateway/requirements.txt
set -a; . ./.env; set +a
.venv/bin/python projects/05-agent-action-gateway/src/slack_approval.py
```

It posts a made-up request (nothing happens, whatever you click) and waits 5 minutes. Click Approve. It prints `{'approved': True, 'approver': 'U...', 'via': 'slack', ...}`, and the message shows who approved. To see a refusal, ask someone who isn't on the list to click first: they get a private note, and an `audit:` line is printed.

## 4. Deploy with Slack

The IDs go to `deploy.sh` through the environment:

```bash
SLACK_CHANNEL_ID=C... SLACK_APPROVERS=U...,U... SLACK_BREAK_GLASS_APPROVERS=U... make up LAYERS="02 05"
```

On a cluster that's already up, use `make deploy LAYERS="02 05"` instead. Without Slack, `APPROVER=cli` uses the CLI approver: answer with `kubectl exec` (see `src/approval.py`).
