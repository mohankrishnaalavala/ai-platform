"""Cost per team (P3-05): the collector's sum connector adds up each model try's cost from the gateway's
traces (llm_cost_by_team_total, helm/otel-collector.yaml). For every team, it must equal the team's
running spend in LiteLLM's team table in Postgres, the number team budgets are checked against. In CI,
chat-default costs $0.00000075 per output token and the mock always returns 10 output tokens; the mock
hosted model has no price.

It compares with the team table, not the spend logs: in 5 CI runs (PR #52, 2026-10-20) LiteLLM wrote no
spend-log row for a team key's streamed call, while the team's spend counted it. The cause isn't proven.
The spend logs are printed for reference only.
Runs in ci.yml's gateway job after test_metrics.py. Skipped unless SPANS_FILE, TEMPO_URL and PROMETHEUS_URL
are set.
"""
import subprocess
import unittest

from test_metrics import PROMETHEUS, SPANS_FILE, TEMPO, query, until
from test_spans import P2_TESTS

TEAM_SPEND = 'select team_alias, spend from "LiteLLM_TeamTable" where spend > 0'
SPEND_LOGS = ('select t.team_alias, sum(s.spend) from "LiteLLM_SpendLogs" s join "LiteLLM_TeamTable" t '
              'on s.team_id = t.team_id group by 1 having sum(s.spend) > 0')


def psql(sql):
    out = subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "litellm", "-d", "litellm",
                          "-Atc", sql], cwd=P2_TESTS, check=True, capture_output=True, text=True).stdout
    return {team: float(v) for team, v in (line.split("|") for line in out.splitlines() if line)}


def traces():
    return {s["metric"].get("metadata_user_api_key_team_alias"): float(s["value"][1])
            for s in query("llm_cost_by_team_total") if float(s["value"][1]) > 0}


def same(a, b):
    return a.keys() == b.keys() and all(abs(a[t] - b[t]) <= 1e-12 + 1e-6 * a[t] for t in a)


@unittest.skipUnless(SPANS_FILE and TEMPO and PROMETHEUS, "needs the CI gateway stack (SPANS_FILE, TEMPO_URL, PROMETHEUS_URL)")
class CostPerTeamTest(unittest.TestCase):
    def test_cost_per_team_equals_the_teams_spend(self):
        def agree():  # both sides lag: LiteLLM updates spend in batches, the collector sends sums in batches
            spend = psql(TEAM_SPEND)
            return spend and same(spend, traces())
        until(agree, seconds=90)
        spend, seen = psql(TEAM_SPEND), traces()
        print(f"\nteam spend (budgets): {spend}\ncost per team from traces: {seen}\n"
              f"spend logs per team (reference): {psql(SPEND_LOGS)}")
        self.assertTrue(spend, "no team spent anything")
        self.assertTrue(same(spend, seen), "traces and the teams' spend disagree")


if __name__ == "__main__":
    unittest.main()
