"""Cost per team (P3-05): the collector's sum connector adds up each model try's cost from the gateway's
traces (llm_cost_by_team_total, helm/otel-collector.yaml). For every team, it must equal what LiteLLM's
spend logs in Postgres say the team spent, the numbers team budgets use. In CI, chat-default costs
$0.00000075 per output token and the mock always returns 10 output tokens; the mock hosted model has no
price.
Runs in ci.yml's gateway job after test_metrics.py. Skipped unless SPANS_FILE, TEMPO_URL and PROMETHEUS_URL
are set.
"""
import subprocess
import unittest

from test_metrics import PROMETHEUS, SPANS_FILE, TEMPO, query, until
from test_spans import P2_TESTS

SPEND = ('select t.team_alias, sum(s.spend) from "LiteLLM_SpendLogs" s join "LiteLLM_TeamTable" t '
         'on s.team_id = t.team_id group by 1 having sum(s.spend) > 0')


def spend_logs():
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "litellm", "-d", "litellm",
                          "-Atc", SPEND], cwd=P2_TESTS, check=True, capture_output=True, text=True).stdout
    return {team: float(v) for team, v in (line.split("|") for line in out.splitlines() if line)}


def traces():
    return {s["metric"].get("metadata_user_api_key_team_alias"): float(s["value"][1])
            for s in query("llm_cost_by_team_total") if float(s["value"][1]) > 0}


def same(a, b):
    return a.keys() == b.keys() and all(abs(a[t] - b[t]) <= 1e-12 + 1e-6 * a[t] for t in a)


@unittest.skipUnless(SPANS_FILE and TEMPO and PROMETHEUS, "needs the CI gateway stack (SPANS_FILE, TEMPO_URL, PROMETHEUS_URL)")
class CostPerTeamTest(unittest.TestCase):
    def test_cost_per_team_equals_the_spend_logs(self):
        def agree():  # both sides lag: LiteLLM writes spend logs in batches, the collector sends sums in batches
            logs = spend_logs()
            return logs and same(logs, traces())
        until(agree, seconds=90)
        logs, seen = spend_logs(), traces()
        print(f"\nspend logs per team: {logs}\ncost per team from traces: {seen}")
        self.assertTrue(logs, "no team spent anything")
        self.assertTrue(same(logs, seen), "traces and spend logs disagree")


if __name__ == "__main__":
    unittest.main()
