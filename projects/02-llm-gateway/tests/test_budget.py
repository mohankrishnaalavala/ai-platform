"""A team with a tiny budget is blocked with a clear error once it has spent it.
chat-default's internal price is $0.00000075 per output token (helm/gateway/values.yaml): the mock in
CI reports 10 output tokens, so a call costs $0.0000075, and a $0.00001 budget allows 2 calls. In a
session the call may reach the hosted model instead, which costs more, so the block comes sooner.
Skipped unless GATEWAY_URL and MASTER_KEY are set."""
import time
import unittest

from client import MASTER, URL, chat, delete_key, new_team_key

BUDGET_USD = 0.00001
MAX_CALLS = 15  # spend is written in batches, so allow a few calls past the budget before failing


@unittest.skipUnless(URL and MASTER, "needs a running gateway (GATEWAY_URL, MASTER_KEY)")
class BudgetTest(unittest.TestCase):
    def test_a_team_over_budget_is_blocked(self):
        key = new_team_key("budget-test", budget_usd=BUDGET_USD)
        try:
            codes = []
            for _ in range(MAX_CALLS):
                status = chat(key, "chat-default", "hello")[0]
                codes.append(status)
                if status != 200:
                    break
                time.sleep(1.5)
            print(f"\nbudget ${BUDGET_USD}: status codes {codes}")
            self.assertGreaterEqual(codes.count(200), 1, "the first call must succeed")
            self.assertEqual(codes[-1], 429, f"never blocked in {MAX_CALLS} calls: {codes}")
        finally:
            delete_key(key)


if __name__ == "__main__":
    unittest.main()
