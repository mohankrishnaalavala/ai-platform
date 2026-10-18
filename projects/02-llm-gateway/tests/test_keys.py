"""A revoked key is rejected.
Skipped unless GATEWAY_URL and MASTER_KEY are set."""
import unittest

from client import MASTER, URL, call, chat, new_team_key


@unittest.skipUnless(URL and MASTER, "needs a running gateway (GATEWAY_URL, MASTER_KEY)")
class KeysTest(unittest.TestCase):
    def test_a_revoked_key_is_rejected(self):
        key = new_team_key("keys-test")
        self.assertEqual(chat(key, "chat-default", "hello")[0], 200)
        self.assertEqual(call("POST", "/key/delete", MASTER, {"keys": [key]})[0], 200)
        self.assertEqual(chat(key, "chat-default", "hello")[0], 401)


if __name__ == "__main__":
    unittest.main()
