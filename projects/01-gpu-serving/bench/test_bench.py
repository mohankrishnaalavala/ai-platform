"""Unit tests for summary.py, upload.py and timings.py. Run: python3 -m unittest discover -s projects/01-gpu-serving/bench"""
import io
import os
import unittest
import urllib.error
from unittest import mock

import summary
import timings
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


# Synthetic pod and events in the shape the Kubernetes API returns, not measurements.
POD = {
    "metadata": {"creationTimestamp": "2026-10-17T01:00:00Z"},
    "status": {"conditions": [
        {"type": "PodScheduled", "status": "True", "lastTransitionTime": "2026-10-17T01:02:00Z"},
        {"type": "Initialized", "status": "True", "lastTransitionTime": "2026-10-17T01:02:05Z"},
        {"type": "Ready", "status": "True", "lastTransitionTime": "2026-10-17T01:05:30Z"},
    ]},
}
EVENTS = {"items": [
    {"reason": "Pulled", "involvedObject": {"fieldPath": "spec.initContainers{gke-gcsfuse-sidecar}"},
     "lastTimestamp": "2026-10-17T01:02:03Z", "message": "sidecar"},
    {"reason": "Pulled", "involvedObject": {"fieldPath": "spec.containers{vllm}"}, "lastTimestamp": None,
     "eventTime": "2026-10-17T01:02:30.123456Z",
     "message": 'Successfully pulled image "us-central1-docker.pkg.dev/my-project/images/vllm-openai@sha256:0" in 25s'},
]}


NODE = {"metadata": {"creationTimestamp": "2026-10-17T01:01:20Z"}}
T0 = 1792198800.0  # 2026-10-17T01:00:00Z


class TimingsTest(unittest.TestCase):
    def test_phases_come_from_the_vllm_container_and_add_up(self):
        p = timings.phases(POD, EVENTS, NODE, T0, T0 + 335)
        self.assertEqual((p["node_ready_s"], p["image_pulled_s"], p["model_loaded_s"], p["first_token_s"]),
                         (120.0, 30.0, 180.0, 5.0))
        self.assertEqual(p["total_s"], 335.0)
        self.assertTrue(p["valid"])

    def test_the_image_path_with_the_project_is_dropped(self):
        note = timings.phases(POD, EVENTS, NODE, T0, T0 + 335)["pulled_message"]
        self.assertNotIn("my-project", note)
        self.assertIn("in 25s", note)

    def test_a_pod_that_is_not_ready_is_an_error(self):
        pod = {"metadata": POD["metadata"], "status": {"conditions": POD["status"]["conditions"][:1]}}
        with self.assertRaises(SystemExit):
            timings.phases(pod, EVENTS, NODE, T0, 0.0)

    def test_warm_node_warm_image_and_restarts_make_a_run_invalid(self):
        old_node = {"metadata": {"creationTimestamp": "2026-10-17T00:30:00Z"}}
        warm = {"items": [dict(EVENTS["items"][1], message='Container image "x" already present on machine')]}
        restarted = dict(POD, status=dict(POD["status"], containerStatuses=[{"name": "vllm", "restartCount": 1}]))
        for args in [(POD, EVENTS, old_node), (POD, warm, NODE), (restarted, EVENTS, NODE)]:
            p = timings.phases(*args, T0, T0 + 335)
            self.assertFalse(p["valid"])
            self.assertEqual(len(p["problems"]), 1)


if __name__ == "__main__":
    unittest.main()
