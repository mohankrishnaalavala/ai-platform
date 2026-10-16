"""Unit tests for summary.py and upload.py. Run: python3 -m unittest discover -s projects/01-gpu-serving/bench"""
import io
import os
import unittest
import urllib.error
from unittest import mock

import summary
import upload

TESTDATA = os.path.join(os.path.dirname(__file__), "testdata")  # synthetic, not results


class SummaryTest(unittest.TestCase):
    def rows(self, text):
        return [line for line in text.splitlines() if line.startswith(("| chat", "| rag"))]

    def test_rows_are_ordered_and_failed_runs_marked_invalid(self):
        text = summary.summary(summary.load(TESTDATA), ["chat", "rag"], [1, 8, 32])
        rows = [r for r in self.rows(text) if "MISSING" not in r]
        self.assertEqual([r.split("|")[1:3] for r in rows],
                         [[" chat ", " 1 "], [" chat ", " 8 "], [" rag ", " 32 "]])
        self.assertTrue(rows[0].endswith("| yes |"))
        self.assertIn("no: failed requests", rows[2])
        self.assertIn("| 101 / 205 / 255 |", rows[1])
        self.assertIn("hash `0000000000000000`", text)

    def test_missing_runs_are_listed_and_flag_the_summary(self):
        text = summary.summary(summary.load(TESTDATA), ["chat", "rag"], [1, 8, 32])
        self.assertIn("INCOMPLETE", text.splitlines()[0])
        self.assertEqual([r.split("|")[1:3] for r in self.rows(text) if "MISSING" in r],
                         [[" chat ", " 32 "], [" rag ", " 1 "], [" rag ", " 8 "]])
        complete = summary.summary(summary.load(TESTDATA), ["chat"], [1, 8])
        self.assertNotIn("INCOMPLETE", complete)

    def test_no_results_is_an_error(self):
        with self.assertRaises(SystemExit):
            summary.load(os.path.dirname(TESTDATA))


class UploadTest(unittest.TestCase):
    def test_upload_is_create_only_and_names_the_object(self):
        with mock.patch.object(upload.urllib.request, "urlopen") as urlopen:
            upload.upload(os.path.join(TESTDATA, "chat-c1.json"), "b", "p1/h/s/chat-c1.json", "tok")
        req = urlopen.call_args[0][0]
        self.assertIn("/b/b/o?uploadType=media&ifGenerationMatch=0&name=p1%2Fh%2Fs%2Fchat-c1.json", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")

    def test_existing_object_fails_instead_of_overwriting(self):
        err = urllib.error.HTTPError("u", 412, "Precondition Failed", {}, io.BytesIO())
        with mock.patch.object(upload.urllib.request, "urlopen", side_effect=err), \
                self.assertRaises(urllib.error.HTTPError):
            upload.upload(os.path.join(TESTDATA, "chat-c1.json"), "b", "x", "tok")

    def test_destination_must_be_a_gs_prefix(self):
        with self.assertRaises(SystemExit):
            upload.main("b/p", ["f"])


if __name__ == "__main__":
    unittest.main()
