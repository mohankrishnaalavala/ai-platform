"""The Slack approver with Slack faked: no network. Who may answer, the break-glass list, refused clicks
audited, auto-reject, Slack down, and what the message shows. The Bolt wiring test needs slack_bolt, so CI
runs it inside the image; CI's stdlib-only unit-test step skips it."""
import contextlib
import importlib.util
import io
import json
import threading
import time
import unittest
from unittest import mock

import approval
import slack_approval

ID = "20261021T153000Z-0a1b2c3d"
REQUEST = {"action_id": ID, "team": "payments", "tool": "scale_deployment", "namespace": "prod", "kind": "Deployment",
           "name": "checkout", "reasons": ["prod write: a <!channel> person must approve"], "approver_team": "",
           "irreversible": False, "dry_run": {"ok": True, "pods_affected": 2, "capacity_change_pct": 100.0,
                                              "pdb_violations": []},
           "diff": '+  "replicas": 4\n```\n<https://evil.example|click me>', "ai_risk_summary": "Looks <@U0EVIL0001> safe.",
           "auto_reject_after_s": 900}
APPROVERS = {"default": ["U0ALICE01"], "break-glass": ["U0BREAK01"]}


class FakeSlack:
    def __init__(self, fail=False):
        self.posted, self.updated, self.ephemeral, self.fail = [], [], [], fail

    def chat_postMessage(self, **kw):
        if self.fail:
            raise ConnectionError("no network")
        self.posted.append(kw)
        return {"ts": "1700000000.000100"}

    def chat_update(self, **kw):
        self.updated.append(kw)

    def chat_postEphemeral(self, **kw):
        self.ephemeral.append(kw)


class MemoryAudit:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(json.loads(json.dumps(record)))


def click(user, choice, action_id=ID):
    return {"type": "block_actions", "user": {"id": user, "username": user.lower()}, "channel": {"id": "C0APPROVE"},
            "actions": [{"action_id": choice, "value": action_id}]}


class Slack(unittest.TestCase):
    def setUp(self):
        self.slack, self.audit = FakeSlack(), MemoryAudit()
        self.approver = slack_approval.SlackApprover(self.slack, "C0APPROVE", APPROVERS, self.audit)

    def decide_while(self, clicks, request=REQUEST, timeout_s=10):
        """decide() in this thread; the clicks come from another, once the message is posted."""
        def run():
            while not self.slack.posted:
                time.sleep(0.01)
            for body in clicks:
                self.approver.on_click(body)
        t = threading.Thread(target=run)
        t.start()
        answer = self.approver.decide(ID, request, timeout_s)
        t.join()
        return answer

    def test_listed_user_approves_and_is_recorded(self):
        a = self.decide_while([click("U0ALICE01", "approve")])
        self.assertEqual((a["approved"], a["approver"], a["via"]), (True, "U0ALICE01", "slack"))
        self.assertEqual(self.slack.updated[0]["ts"], "1700000000.000100")
        self.assertIn("*Approved* by <@U0ALICE01>", json.dumps(self.slack.updated[0]["blocks"]))
        self.assertNotIn('"actions"', json.dumps(self.slack.updated[0]["blocks"]))  # the buttons are gone

    def test_reject(self):
        a = self.decide_while([click("U0ALICE01", "reject")])
        self.assertEqual((a["approved"], a["approver"]), (False, "U0ALICE01"))

    def test_unlisted_user_is_refused_audited_and_the_request_keeps_waiting(self):
        a = self.decide_while([click("U0MALLORY", "approve"), click("U0ALICE01", "reject")])
        self.assertEqual((a["approved"], a["approver"]), (False, "U0ALICE01"))
        self.assertEqual([(r["action_id"], r["record"], r["refused_click"]["slack_user"]) for r in self.audit.records],
                         [(ID, "refused-1", "U0MALLORY")])
        self.assertIn("not on the default approver list", self.slack.ephemeral[0]["text"])

    def test_a_refusal_that_cant_be_audited_is_loud(self):
        class BrokenAudit:
            def write(self, record):
                raise OSError("bucket unreachable")
        self.approver.audit = BrokenAudit()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = self.decide_while([click("U0MALLORY", "approve"), click("U0ALICE01", "reject")])
        self.assertEqual((a["approved"], a["approver"]), (False, "U0ALICE01"))  # still refused; the request went on
        self.assertEqual(self.approver.unaudited_refusals, 1)
        self.assertIn("ERROR slack: a refused click on", err.getvalue())
        self.assertIn("NOT audited (OSError)", err.getvalue())

    def test_break_glass_takes_only_the_break_glass_list(self):
        bg = REQUEST | {"approver_team": "break-glass"}
        a = self.decide_while([click("U0ALICE01", "approve"), click("U0BREAK01", "approve")], request=bg)
        self.assertEqual((a["approved"], a["approver"]), (True, "U0BREAK01"))
        self.assertEqual(self.audit.records[0]["refused_click"]["approver_team"], "break-glass")
        self.assertIn("Only the break-glass approvers", json.dumps(self.slack.posted[0]["blocks"]))

    def test_no_answer_is_an_auto_reject(self):
        a = self.approver.decide(ID, REQUEST, 0.05)
        self.assertEqual((a["approved"], a["approver"]), (False, None))
        self.assertIn("auto-reject", a["via"])
        self.assertIn("Auto-rejected", json.dumps(self.slack.updated[0]["blocks"]))
        self.approver.on_click(click("U0ALICE01", "approve"))  # a late click changes nothing
        self.assertIn("no longer waiting", self.slack.ephemeral[0]["text"])

    def test_slack_down_rejects_at_once(self):
        approver = slack_approval.SlackApprover(FakeSlack(fail=True), "C0APPROVE", APPROVERS, self.audit)
        a = approver.decide(ID, REQUEST, 10)
        self.assertEqual((a["approved"], a["via"]), (False, "slack unavailable (ConnectionError)"))

    def test_team_without_a_list_rejects_at_once(self):
        a = self.approver.decide(ID, REQUEST | {"approver_team": "dba"}, 10)
        self.assertEqual((a["approved"], a["via"]), (False, "slack: no approvers listed for team dba"))
        self.assertEqual(self.slack.posted, [])

    def test_message_keeps_facts_apart_from_the_ai_summary_and_escapes_request_text(self):
        self.decide_while([click("U0ALICE01", "reject")])
        b = self.slack.posted[0]["blocks"]
        facts, diff, summary = b[0]["text"]["text"], b[1]["text"]["text"], b[3]["elements"][0]["text"]
        self.assertIn("needs approval", facts)
        self.assertIn("&lt;!channel&gt;", facts)
        self.assertNotIn("<!channel>", json.dumps(b))
        self.assertNotIn("<https://evil", diff)
        self.assertEqual(diff.count("```"), 2)  # the diff can't close its code block early
        self.assertTrue(summary.startswith("*AI summary*"))
        self.assertIn("&lt;@U0EVIL0001&gt;", summary)
        self.assertEqual([e["action_id"] for e in b[-1]["elements"]], ["approve", "reject"])
        self.assertEqual({e["value"] for e in b[-1]["elements"]}, {ID})

    def test_bad_config_stops_the_server(self):
        for channel, approvers in [("#approvals", APPROVERS), ("C0APPROVE", {"default": ["alice"]}),
                                   ("C0APPROVE", {"default": "U0ALICE01"})]:
            with self.assertRaises(ValueError):
                slack_approval.SlackApprover(self.slack, channel, approvers, self.audit)


class Bounded(unittest.TestCase):
    def test_caps_waiting_approvals(self):
        release = threading.Event()

        class Slow:
            def decide(self, action_id, request, timeout_s):
                release.wait(5)
                return {"approved": True, "approver": "x", "via": "test"}
        b = approval.Bounded(Slow(), 1)
        t = threading.Thread(target=b.decide, args=(ID, {}, 5))
        t.start()
        time.sleep(0.05)
        self.assertEqual(b.decide(ID, {}, 5)["via"], "rejected at once: 1 approvals already waiting")
        release.set()
        t.join()
        self.assertTrue(b.decide(ID, {}, 5)["approved"])  # the slot is free again


@unittest.skipUnless(importlib.util.find_spec("slack_bolt"), "needs slack_bolt (runs inside the image)")
class BoltWiring(unittest.TestCase):
    def test_buttons_reach_the_approver(self):
        from slack_bolt import App
        from slack_bolt.authorization import AuthorizeResult
        from slack_bolt.request import BoltRequest

        def offline(*args, **kwargs):
            raise AssertionError("the test tried to reach the network")
        # An offline workspace: authorize answers locally, so Bolt never calls Slack's auth.test.
        app = App(signing_secret="unused", authorize=lambda **kw: AuthorizeResult(
            enterprise_id=None, team_id="T0TEAM001", bot_token="xoxb-offline", bot_id="B0BOT0001", bot_user_id="U0BOT0001"))
        slack, seen = FakeSlack(), []
        approver = slack_approval.SlackApprover(slack, "C0APPROVE", APPROVERS, MemoryAudit())
        approver.on_click = seen.append
        slack_approval.register(app, approver)
        with mock.patch("socket.socket.connect", offline), mock.patch("socket.create_connection", offline):
            for choice in ("approve", "reject"):
                body = click("U0ALICE01", choice) | {"team": {"id": "T0TEAM001"}}
                self.assertEqual(app.dispatch(BoltRequest(body=body, mode="socket_mode")).status, 200)
        for _ in range(200):  # Bolt acks first, then runs the listener in its own thread
            if len(seen) == 2:
                break
            time.sleep(0.01)
        self.assertEqual([b["actions"][0]["action_id"] for b in seen], ["approve", "reject"])


if __name__ == "__main__":
    unittest.main()
