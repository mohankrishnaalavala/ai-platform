import contextlib
import io
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import approval

ID = "20261021T153000Z-0a1b2c3d"


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"APPROVALS_DIR": self.dir})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.approver = approval.CliApprover(self.dir, poll_s=0.01)

    def answer_when_pending(self, *argv):
        def run():
            req, _ = self.approver.paths(ID)
            while not os.path.exists(req):
                time.sleep(0.01)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(approval.main(["list"]), 0)
            self.assertIn('"tool": "scale_deployment"', out.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(approval.main(list(argv)), 0)
        t = threading.Thread(target=run)
        t.start()
        return t

    def test_approve_records_who(self):
        t = self.answer_when_pending("approve", ID, "--by", "alice")
        a = self.approver.decide(ID, {"tool": "scale_deployment"}, 10)
        t.join()
        self.assertEqual(a, {"approved": True, "approver": "alice", "via": "cli"})
        self.assertEqual(os.listdir(self.dir), [])  # request and answer cleaned up

    def test_reject(self):
        t = self.answer_when_pending("reject", ID, "--by", "bob")
        a = self.approver.decide(ID, {"tool": "scale_deployment"}, 10)
        t.join()
        self.assertEqual((a["approved"], a["approver"]), (False, "bob"))

    def test_no_answer_is_an_auto_reject(self):
        a = self.approver.decide(ID, {"tool": "scale_deployment"}, 0.05)
        self.assertEqual((a["approved"], a["approver"]), (False, None))
        self.assertIn("auto-reject", a["via"])

    def test_refuses_unknown_or_malformed_ids(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(approval.main(["approve", ID, "--by", "alice"]), 1)  # nothing pending
            self.assertEqual(approval.main(["approve", "../../etc/passwd", "--by", "alice"]), 2)
            self.assertEqual(approval.main(["approve", ID + "\n", "--by", "alice"]), 2)
            self.assertEqual(approval.main(["approve", ID, "--by", " "]), 2)


if __name__ == "__main__":
    unittest.main()
