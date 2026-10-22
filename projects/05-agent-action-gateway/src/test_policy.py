import io
import json
import unittest
import urllib.error

import policy


def answering(body):
    calls = []

    def urlopen(req, timeout):
        calls.append(req)
        if isinstance(body, Exception):
            raise body
        return io.BytesIO(json.dumps(body).encode())
    return urlopen, calls


class Decide(unittest.TestCase):
    def test_passes_the_facts_and_returns_the_decision(self):
        urlopen, calls = answering({"result": {"decision": "needs_approval", "reasons": ["break-glass"], "policy_version": "1",
                                               "approver_team": "break-glass", "irreversible": False}})
        d = policy.decide({"tool": "scale_deployment"}, urlopen=urlopen)
        self.assertEqual(d, {"decision": "needs_approval", "reasons": ["break-glass"], "policy_version": "1",
                             "approver_team": "break-glass", "irreversible": False})
        self.assertEqual(json.loads(calls[0].data), {"input": {"tool": "scale_deployment"}})
        self.assertEqual(calls[0].full_url, "http://127.0.0.1:8181/v1/data/gateway/result")  # policies/gateway.rego's result

    def test_fails_closed(self):
        for body in (urllib.error.URLError("refused"), TimeoutError(), {}, {"result": {}},
                     {"result": {"decision": "maybe", "reasons": []}}, {"result": {"decision": "allow", "reasons": "x"}}):
            urlopen, _ = answering(body)
            d = policy.decide({}, urlopen=urlopen)
            self.assertEqual(d["decision"], "deny", body)
            self.assertIn("policy engine", d["reasons"][0])


if __name__ == "__main__":
    unittest.main()
