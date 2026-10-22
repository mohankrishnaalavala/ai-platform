#!/usr/bin/env python3
"""The Slack approver: asks in one Slack channel, with Approve and Reject buttons, through Socket Mode. The
pod opens a WebSocket out to Slack and nothing in the cluster listens, so there is no endpoint to forge.
Same method as the CLI approver (approval.py): decide(action_id, request, timeout_s).

- Who may answer: the Slack user IDs listed for the request's approver team (SLACK_APPROVERS, e.g.
  {"default": ["U0123ABCD"], "break-glass": ["U0456EFGH"]}; "default" answers requests that name no
  team). A break-glass request takes only the break-glass list. A click from anyone else is refused, told
  so, and audited (record "refused-N"); the request keeps waiting. A team with no list is rejected at once.
- The approver recorded is the Slack user ID from Slack's own payload, not a name someone typed.
- No answer within the timeout (15 minutes in the server) is a reject, and the message says so. If Slack
  can't be reached, the request is rejected at once (fail closed).
- The message shows the policy's decision and reasons, the dry run and the diff, and apart from them,
  labelled, the AI summary. Text from the request is escaped, so a manifest can't add a mention or a link.
- Tokens: server.py reads them from the mounted Secret Manager files and hands them to Slack's client.
  They are never logged. slack_bolt is imported only by connect(), so the rest is tested without it.

Slack end to end from a laptop, no cluster (needs the app from slack-app-manifest.yaml; see slack.md):
  SLACK_APP_TOKEN=xapp-... SLACK_BOT_TOKEN=xoxb-... SLACK_CHANNEL=C0123ABCD SLACK_APPROVERS=U0123ABCD \\
    python slack_approval.py
It posts one made-up request and prints the answer."""
import datetime
import json
import os
import re
import sys
import threading

CHANNEL = re.compile(r"[CG][A-Z0-9]{6,}")
USER = re.compile(r"[UW][A-Z0-9]{6,}")
TEXT_MAX = 2900  # Slack takes up to 3000 characters in one text field


def esc(value):
    """Slack's escaping, so text shows as typed: no <@mention>, <!channel> or <link|label>."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cut(text, limit=TEXT_MAX):
    return text if len(text) <= limit else text[:limit] + "\n... (cut; the audit record has all of it)"


def blocks(action_id, r, outcome=None):
    """The message: facts first, the AI summary apart and labelled, then the buttons (or the outcome)."""
    dry = r.get("dry_run") or {}
    facts = [(f"*{esc(r.get('tool'))}* on {esc(r.get('kind'))} *{esc(r.get('namespace'))}/{esc(r.get('name'))}*, "
              f"asked by an agent of team *{esc(r.get('team'))}*"),
             "*Policy decision:* needs approval", *[f"- {esc(x)}" for x in r.get("reasons") or []],
             (f"*Dry run:* {esc(dry.get('pods_affected'))} pods affected, capacity change "
              f"{esc(dry.get('capacity_change_pct'))}%, PodDisruptionBudget violations: "
              f"{esc(', '.join(dry.get('pdb_violations') or []) or 'none')}")]
    if r.get("approver_team"):
        facts.append(f"*Only the {esc(r['approver_team'])} approvers can answer this.*")
    if r.get("irreversible"):
        facts.append("*Irreversible:* undo can't restore this change.")
    diff = esc(r.get("diff") or "(no diff)").replace("```", "'''")  # a diff can't end the code block early
    out = [{"type": "section", "text": {"type": "mrkdwn", "text": cut("\n".join(facts))}},
           {"type": "section", "text": {"type": "mrkdwn", "text": f"*Diff*\n```{cut(diff)}```"}},
           {"type": "divider"},
           {"type": "context", "elements": [{"type": "mrkdwn", "text": cut(
               "*AI summary* (an LLM explains the facts above; the policy decided, not the LLM):\n"
               + esc(r.get("ai_risk_summary") or "unavailable"))}]},
           {"type": "context", "elements": [{"type": "mrkdwn", "text":
               f"Action {esc(action_id)} · auto-reject after {float(r.get('auto_reject_after_s') or 900) / 60:g} min"}]}]
    if outcome:
        out.append({"type": "section", "text": {"type": "mrkdwn", "text": outcome}})
    else:
        out.append({"type": "actions", "elements": [
            {"type": "button", "action_id": "approve", "value": action_id, "style": "primary",
             "text": {"type": "plain_text", "text": "Approve"}},
            {"type": "button", "action_id": "reject", "value": action_id, "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"}}]})
    return out


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SlackApprover:
    def __init__(self, client, channel, approvers, audit_log):
        """client: Slack's WebClient (chat_postMessage, chat_update, chat_postEphemeral) or a fake.
        approvers: {"default": [user IDs], "break-glass": [user IDs], ...}. Bad config stops the server."""
        if not isinstance(channel, str) or not CHANNEL.fullmatch(channel):
            raise ValueError("SLACK_CHANNEL must be a channel ID like C0123ABCD")
        if not isinstance(approvers, dict) or not all(
                isinstance(ids, list) and all(isinstance(u, str) and USER.fullmatch(u) for u in ids) for ids in approvers.values()):
            raise ValueError('SLACK_APPROVERS must map approver teams to lists of user IDs, like {"default": ["U0123ABCD"]}')
        self.client, self.channel, self.approvers, self.audit = client, channel, approvers, audit_log
        self.pending, self.lock = {}, threading.Lock()

    def decide(self, action_id, request, timeout_s):
        team = request.get("approver_team") or "default"
        if not self.approvers.get(team):
            return {"approved": False, "approver": None, "via": f"slack: no approvers listed for team {team}"}
        wait = {"request": request, "team": team, "event": threading.Event(), "answer": None, "refused": 0}
        with self.lock:
            self.pending[action_id] = wait
        try:
            text = f"Approval needed: {request.get('tool')} in {request.get('namespace')}"
            try:
                ts = self.client.chat_postMessage(channel=self.channel, text=text, blocks=blocks(action_id, request))["ts"]
            except Exception as e:  # noqa: BLE001 - Slack down or a bad token: nobody can approve, so reject now
                return {"approved": False, "approver": None, "via": f"slack unavailable ({type(e).__name__})"}
            wait["event"].wait(timeout_s)
            with self.lock:  # popped under the lock, so a click from now on finds nothing pending
                answer = self.pending.pop(action_id)["answer"]
            if answer is None:
                answer = {"approved": False, "approver": None, "via": f"auto-reject: no answer in {timeout_s:g} s"}
                outcome = f"*Auto-rejected:* no answer in {timeout_s / 60:g} min."
            else:
                outcome = f"*{'Approved' if answer['approved'] else 'Rejected'}* by <@{answer['approver']}>."
            try:  # best effort: the answer stands even if the message can't be changed
                self.client.chat_update(channel=self.channel, ts=ts, text=text, blocks=blocks(action_id, request, outcome))
            except Exception as e:  # noqa: BLE001
                print(f"slack: couldn't update the message for {action_id} ({type(e).__name__})", file=sys.stderr)
            return answer
        finally:
            with self.lock:
                self.pending.pop(action_id, None)

    def on_click(self, body):
        """A button click, as Slack sends it (block_actions). Bolt has already acknowledged it."""
        user, click = body.get("user") or {}, (body.get("actions") or [{}])[0]
        uid, action_id, choice = user.get("id"), click.get("value"), click.get("action_id")
        refused = None
        with self.lock:
            wait = self.pending.get(action_id)
            if wait is None or wait["answer"] is not None or choice not in ("approve", "reject"):
                note = "This request is no longer waiting (answered, auto-rejected or unknown)."
            elif uid not in self.approvers.get(wait["team"], []):
                wait["refused"] += 1
                refused = {"action_id": action_id, "record": f"refused-{wait['refused']}", "time": _now(),
                           "team": wait["request"].get("team"),
                           "refused_click": {"slack_user": uid, "answer": choice, "approver_team": wait["team"],
                                             "reason": f"not on the {wait['team']} approver list"}}
                note = f"You're not on the {wait['team']} approver list, so your answer doesn't count. This was recorded."
            else:
                wait["answer"] = {"approved": choice == "approve", "approver": uid, "via": "slack",
                                  "approver_name": user.get("username") or user.get("name")}
                wait["event"].set()
                note = None
        if refused:
            try:
                self.audit.write(refused)
            except Exception as e:  # noqa: BLE001 - the click is refused either way
                print(f"slack: the refused click on {action_id} wasn't audited ({type(e).__name__})", file=sys.stderr)
        if note and uid:
            try:
                self.client.chat_postEphemeral(channel=(body.get("channel") or {}).get("id", self.channel), user=uid, text=note)
            except Exception as e:  # noqa: BLE001 - only a courtesy note
                print(f"slack: couldn't send the note on {action_id} ({type(e).__name__})", file=sys.stderr)


def register(app, approver):
    """Send both buttons' clicks (a Bolt app's block_actions) to the approver, acknowledged first."""
    def click(ack, body):
        ack()
        approver.on_click(body)
    app.action("approve")(click)
    app.action("reject")(click)


def connect(app_token, bot_token, channel, approvers, audit_log):
    """The approver, connected to Slack through Socket Mode (a background thread that reconnects by itself)."""
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    app = App(token=bot_token)  # checks the bot token with Slack first: a bad token stops the server
    approver = SlackApprover(app.client, channel, approvers, audit_log)
    register(app, approver)
    SocketModeHandler(app, app_token).connect()
    return approver


class _PrintAudit:
    def write(self, record):
        print("audit:", json.dumps(record))


if __name__ == "__main__":
    e = os.environ.get
    ids = [u for u in (e("SLACK_APPROVERS") or "").split(",") if u]
    approver = connect(e("SLACK_APP_TOKEN"), e("SLACK_BOT_TOKEN"), e("SLACK_CHANNEL"), {"default": ids}, _PrintAudit())
    timeout = float(e("TIMEOUT_S", "300"))
    print(f"posted a test request to {e('SLACK_CHANNEL')}; click Approve or Reject within {timeout:g} s")
    print(approver.decide("20261022T120000Z-0a1b2c3d", {
        "tool": "scale_deployment", "namespace": "prod", "kind": "Deployment", "name": "checkout", "team": "payments",
        "reasons": ["a test request from slack_approval.py: nothing happens whatever you click"],
        "dry_run": {"ok": True, "pods_affected": 2, "capacity_change_pct": 100.0, "pdb_violations": []},
        "diff": '-    "replicas": 2,\n+    "replicas": 4,', "ai_risk_summary": "unavailable",
        "auto_reject_after_s": timeout}, timeout))
