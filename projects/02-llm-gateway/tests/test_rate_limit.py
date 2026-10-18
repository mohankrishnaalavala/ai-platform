"""Going over a team's requests-per-minute limit returns 429.
Skipped unless GATEWAY_URL and MASTER_KEY are set."""
import unittest

from client import MASTER, URL, chat, delete_key, new_team_key


@unittest.skipUnless(URL and MASTER, "needs a running gateway (GATEWAY_URL, MASTER_KEY)")
class RateLimitTest(unittest.TestCase):
    def test_the_third_call_in_a_minute_is_refused(self):
        key = new_team_key("rpm-test", rpm=2)
        try:
            codes = [chat(key, "chat-default", "hello")[0] for _ in range(4)]
            print(f"\nrpm 2: status codes {codes}")
            self.assertEqual(codes[:2], [200, 200])
            self.assertIn(429, codes[2:])
        finally:
            delete_key(key)


if __name__ == "__main__":
    unittest.main()
