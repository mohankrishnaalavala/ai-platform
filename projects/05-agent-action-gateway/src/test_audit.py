import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse

import audit


class File(unittest.TestCase):
    def test_appends_and_reads_back(self):
        path = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
        log = audit.FileAudit(path)
        self.assertIsNone(log.read("20261021T153000Z-00000000"))
        log.write({"action_id": "20261021T153000Z-00000001", "result": {"status": "executed"}})
        log.write({"action_id": "20261021T153000Z-00000002", "result": {"status": "denied"}})
        self.assertEqual(log.read("20261021T153000Z-00000001")["result"]["status"], "executed")
        with open(path) as f:
            self.assertEqual(len(f.readlines()), 2)


class Gcs(unittest.TestCase):
    def fake(self, fail=None):
        calls = []

        def urlopen(req, timeout):
            calls.append(req)
            if req.full_url == audit.TOKEN_URL:
                return io.BytesIO(b'{"access_token": "ya29.test"}')
            if fail:
                raise urllib.error.HTTPError(req.full_url, fail, "x", {}, io.BytesIO(b""))
            return io.BytesIO(json.dumps({"action_id": "20261021T153000Z-00000001"}).encode())
        return urlopen, calls

    def test_write_is_create_only_with_the_metadata_token(self):
        urlopen, calls = self.fake()
        audit.GcsAudit("audit-bucket", urlopen=urlopen).write({"action_id": "20261021T153000Z-00000001"})
        token_req, upload = calls
        self.assertEqual(token_req.get_header("Metadata-flavor"), "Google")
        url = urllib.parse.urlparse(upload.full_url)
        self.assertEqual(url.path, "/upload/storage/v1/b/audit-bucket/o")
        self.assertEqual(urllib.parse.parse_qs(url.query),
                         {"uploadType": ["media"], "name": ["20261021T153000Z-00000001.json"], "ifGenerationMatch": ["0"]})
        self.assertEqual(upload.get_header("Authorization"), "Bearer ya29.test")
        self.assertEqual(upload.get_method(), "POST")

    def test_read(self):
        urlopen, calls = self.fake()
        rec = audit.GcsAudit("audit-bucket", urlopen=urlopen).read("20261021T153000Z-00000001")
        self.assertEqual(rec["action_id"], "20261021T153000Z-00000001")
        self.assertTrue(calls[1].full_url.endswith("/storage/v1/b/audit-bucket/o/20261021T153000Z-00000001.json?alt=media"))
        urlopen, _ = self.fake(fail=404)
        self.assertIsNone(audit.GcsAudit("b", urlopen=urlopen).read("20261021T153000Z-00000009"))
        urlopen, _ = self.fake(fail=403)
        with self.assertRaises(urllib.error.HTTPError):
            audit.GcsAudit("b", urlopen=urlopen).read("20261021T153000Z-00000009")


if __name__ == "__main__":
    unittest.main()
