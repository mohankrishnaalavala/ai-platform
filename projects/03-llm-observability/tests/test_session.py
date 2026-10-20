"""P3 checks in a session (run.sh runs them through port-forwards):
- Grafana answers, and every dashboard in dashboards/ is loaded
- Prometheus scrapes the collector's span metrics and kube-state-metrics (and vLLM when VLLM_DEPLOYED=1),
  and has rules loaded once slos/ exists
- a few gateway calls reach Tempo as traces and Prometheus as span metrics for the calling team
- the alert receiver answers, when deployed (RECEIVER_URL)
- FallbackAlertTest (only with FALLBACK_ALERT=1, no vLLM): chat-default calls every 2 s all fall back,
  until the fallback alert reaches the receiver; prints the time to alert
Parts that later P3 pull requests build are skipped until their files (dashboards/, slos/) or their Service
exist. Skipped unless GRAFANA_URL, PROMETHEUS_URL, TEMPO_URL, GATEWAY_URL and MASTER_KEY are set.
"""
import json
import os
import pathlib
import time
import unittest
import urllib.parse
import urllib.request

from test_metrics import PROMETHEUS, TEMPO, get, query, until
from test_spans import MASTER, URL, chat, onboard_team  # P2's test client

GRAFANA, RECEIVER = os.environ.get("GRAFANA_URL", ""), os.environ.get("RECEIVER_URL", "")
P3 = pathlib.Path(__file__).resolve().parent.parent
ALERT_WAIT_S = 20 * 60  # the fallback alert needs 10 min over its threshold, plus rule and scrape intervals


def new_team(tag):
    name = f"p3-{tag}-{int(time.time() * 1000) % 10**8}"
    return name, onboard_team.create_key(URL, MASTER, onboard_team.create_team(URL, MASTER, name, 1, 100), f"{name}-1")


def alerts():
    """Every alert in the receiver's log (one Alertmanager webhook body per line)."""
    with urllib.request.urlopen(RECEIVER + "/log", timeout=10) as r:
        lines = r.read().decode().splitlines()
    return [a for line in lines if line.strip() for a in json.loads(line).get("alerts", [])]


@unittest.skipUnless(GRAFANA and PROMETHEUS and TEMPO and URL and MASTER, "needs a session (run.sh)")
class SessionTest(unittest.TestCase):
    def test_grafana_answers(self):
        self.assertEqual(get(GRAFANA + "/api/health").get("database"), "ok")

    def test_dashboards_from_git_are_loaded(self):
        files = sorted((P3 / "dashboards").glob("*.json"))
        if not files:
            self.skipTest("no dashboards/ yet (the dashboards pull request adds them)")
        for f in files:
            uid = json.loads(f.read_text())["uid"]
            self.assertEqual(get(f"{GRAFANA}/api/dashboards/uid/{uid}")["dashboard"]["uid"], uid, f.name)

    def test_scrape_targets_are_up(self):
        wanted = ["otel-collector", "kube-state-metrics"] + (["vllm"] if os.environ.get("VLLM_DEPLOYED") == "1" else [])

        def up():
            targets = get(PROMETHEUS + "/api/v1/targets?state=active")["data"]["activeTargets"]
            return all(any(w in t["scrapePool"] and t["health"] == "up" for t in targets) for w in wanted)
        self.assertTrue(until(up, 90), f"not every target is up: {wanted}")

    def test_rules_are_loaded(self):
        if not (P3 / "slos").is_dir():
            self.skipTest("no slos/ yet (the SLO pull request adds them)")
        self.assertTrue(get(PROMETHEUS + "/api/v1/rules")["data"]["groups"])

    def test_gateway_calls_reach_tempo_and_span_metrics(self):
        team, key = new_team("session")
        statuses = [chat(key, "chat-default", "Reply with the single word: ok")[0] for _ in range(3)]
        print(f"\ngateway calls as {team}: {statuses}")
        self.assertIn(200, statuses)
        q = f'traces_span_metrics_calls_total{{span_name="litellm_request",metadata_user_api_key_team_alias="{team}"}}'
        self.assertTrue(until(lambda: query(q), 120), "no span metrics for the calling team")
        search = TEMPO + "/api/search?" + urllib.parse.urlencode({"q": '{ name = "litellm_request" }', "limit": 5})
        self.assertTrue(until(lambda: get(search).get("traces"), 120), "no litellm_request trace in Tempo")

    def test_alert_receiver_answers(self):
        if not RECEIVER:
            self.skipTest("no alert receiver deployed yet (the SLO pull request adds it)")
        alerts()


@unittest.skipUnless(os.environ.get("FALLBACK_ALERT") == "1" and RECEIVER and URL and MASTER,
                     "only in a session with no vLLM, with the alert receiver (FALLBACK_ALERT=1)")
class FallbackAlertTest(unittest.TestCase):
    def test_the_fallback_alert_fires(self):
        _, key = new_team("fallback-alert")
        start, sent, fired = time.monotonic(), 0, None
        while time.monotonic() - start < ALERT_WAIT_S:
            chat(key, "chat-default", "Reply with the single word: ok")
            sent += 1
            if sent % 5 == 0:  # look every ~10 s
                fired = next((a for a in alerts() if "fallback" in a.get("labels", {}).get("alertname", "").lower()
                              and a.get("status") == "firing"), None)
                if fired:
                    break
            time.sleep(2)
        took = time.monotonic() - start
        print(f"\nfallback alert: {'fired' if fired else 'did not fire'} after {took:.0f} s ({sent} requests, "
              f"one every 2 s); alertname {fired and fired['labels']['alertname']}, startsAt {fired and fired.get('startsAt')}")
        self.assertTrue(fired)


if __name__ == "__main__":
    unittest.main()
