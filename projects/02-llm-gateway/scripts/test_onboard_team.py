"""Unit tests for onboard_team.py, with the gateway's admin API mocked.
Run: python3 -m unittest discover -s projects/02-llm-gateway/scripts"""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import onboard_team


def responses(*bodies):
    """urlopen stand-in that returns these JSON bodies in order and records each request."""
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        r = mock.MagicMock()
        r.__enter__.return_value.read.return_value = json.dumps(bodies[len(calls) - 1]).encode()
        return r
    return urlopen, calls


class OnboardTeamTest(unittest.TestCase):
    def test_creates_the_team_then_one_key_and_prints_the_key_once(self):
        urlopen, calls = responses([], {"team_id": "t-1"}, {"key": "sk-test123"})
        out = io.StringIO()
        env = {"GATEWAY_URL": "http://gw", "LITELLM_MASTER_KEY": "sk-master"}
        with mock.patch.object(onboard_team.urllib.request, "urlopen", urlopen), \
                mock.patch.dict(onboard_team.os.environ, env, clear=True), redirect_stdout(out):
            onboard_team.main(["payments", "20", "60"])
        self.assertEqual([c.full_url for c in calls], ["http://gw/team/list", "http://gw/team/new", "http://gw/key/generate"])
        self.assertEqual(json.loads(calls[1].data), {"team_alias": "payments", "max_budget": 20.0, "rpm_limit": 60})
        self.assertEqual(json.loads(calls[2].data), {"team_id": "t-1", "key_alias": "payments-1"})
        self.assertEqual(calls[1].get_header("Authorization"), "Bearer sk-master")
        self.assertEqual(out.getvalue().count("sk-test123"), 1)
        self.assertNotIn("sk-master", out.getvalue())

    def test_a_github_actions_run_masks_the_key_first(self):
        urlopen, _ = responses([], {"team_id": "t-1"}, {"key": "sk-test123"})
        out = io.StringIO()
        env = {"GATEWAY_URL": "http://gw", "LITELLM_MASTER_KEY": "sk-master", "GITHUB_ACTIONS": "true"}
        with mock.patch.object(onboard_team.urllib.request, "urlopen", urlopen), \
                mock.patch.dict(onboard_team.os.environ, env, clear=True), redirect_stdout(out):
            onboard_team.main(["payments", "20", "60"])
        self.assertTrue(out.getvalue().startswith("::add-mask::sk-test123\n"))

    def test_an_existing_team_name_is_refused(self):
        urlopen, calls = responses([{"team_alias": "payments"}])
        with mock.patch.object(onboard_team.urllib.request, "urlopen", urlopen), self.assertRaises(SystemExit):
            onboard_team.create_team("http://gw", "sk-master", "payments", 20.0, 60)
        self.assertEqual(len(calls), 1)

    def test_bad_arguments_are_refused(self):
        for argv in [["Payments", "20", "60"], ["payments", "0", "60"], ["payments", "20", "1.5"], ["p", "20", "60"],
                     ["payments", "x", "60"], ["payments", "20"]]:
            with self.assertRaises(SystemExit, msg=argv):
                onboard_team.parse(argv)
        self.assertEqual(onboard_team.parse(["data-science", "5.5", "10", "1000"]), ("data-science", 5.5, 10, 1000))


if __name__ == "__main__":
    unittest.main()
