import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse

import kube


class Opener:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.requests = status, body if body is not None else {"kind": "Deployment"}, []

    def open(self, req, timeout):
        self.requests.append(req)
        data = io.BytesIO(json.dumps(self.body).encode())
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "err", {}, data)
        return data


class Client(unittest.TestCase):
    def setUp(self):
        self.sa = tempfile.mkdtemp()
        with open(os.path.join(self.sa, "token"), "w") as f:
            f.write("sa-token\n")

    def client(self, opener):
        return kube.Kube(base="https://k8s", sa_dir=self.sa, opener=opener)

    def test_dry_run_patch(self):
        o = Opener()
        self.client(o).send("Deployment", "dev", "checkout", {"type": "merge", "body": {"spec": {"replicas": 3}}}, dry_run=True)
        req = o.requests[0]
        url = urllib.parse.urlparse(req.full_url)
        self.assertEqual((req.get_method(), url.path), ("PATCH", "/apis/apps/v1/namespaces/dev/deployments/checkout"))
        self.assertEqual(urllib.parse.parse_qs(url.query), {"dryRun": ["All"], "fieldManager": ["action-gateway"]})
        self.assertEqual(req.get_header("Content-type"), "application/merge-patch+json")
        self.assertEqual(req.get_header("Authorization"), "Bearer sa-token")
        self.assertEqual(json.loads(req.data), {"spec": {"replicas": 3}})

    def test_apply_and_delete(self):
        o = Opener()
        c = self.client(o)
        c.send("ConfigMap", "dev", "flags", {"type": "apply", "body": {"kind": "ConfigMap"}}, dry_run=False)
        self.assertEqual(o.requests[0].get_header("Content-type"), "application/apply-patch+yaml")
        self.assertIn("force=true", o.requests[0].full_url)
        self.assertNotIn("dryRun", o.requests[0].full_url)
        delete = {"type": "delete", "body": {"preconditions": {"uid": "u"}}}
        self.assertIsNone(c.send("ConfigMap", "dev", "flags", delete, dry_run=True))
        self.assertIsNone(c.send("ConfigMap", "dev", "flags", delete, dry_run=False))
        dry, real = o.requests[1:]
        # with a body the API server ignores ?dryRun, so a dry-run delete must say so in the body (found in kind)
        self.assertEqual((dry.get_method(), json.loads(dry.data)), ("DELETE", {"preconditions": {"uid": "u"}, "dryRun": ["All"]}))
        self.assertEqual(json.loads(real.data), {"preconditions": {"uid": "u"}})
        self.assertNotIn("?", dry.full_url)

    def test_errors(self):
        c = self.client(Opener(403, {"message": 'deployments.apps "x" is forbidden'}))
        with self.assertRaises(kube.KubeError) as e:
            c.get("Deployment", "kube-system", "x")
        self.assertEqual((e.exception.status, str(e.exception)), (403, 'HTTP 403: deployments.apps "x" is forbidden'))
        self.assertIsNone(self.client(Opener(404, {})).get("Deployment", "dev", "x"))
        self.assertIsNone(c.namespace_env("kube-system"))

    def test_transport_failures_are_kube_errors(self):
        class Down:
            def __init__(self, error):
                self.error = error

            def open(self, req, timeout):
                raise self.error

        class Garbled:
            def open(self, req, timeout):
                return io.BytesIO(b"<html>proxy error</html>")
        for opener in (Down(urllib.error.URLError("connection refused")), Down(TimeoutError("timed out")),
                       Down(ConnectionResetError()), Garbled()):
            c = self.client(opener)
            with self.assertRaises(kube.KubeError) as e:
                c.get("Deployment", "dev", "checkout")  # not None: only a 404 means "doesn't exist"
            self.assertEqual(e.exception.status, 0)
            self.assertIsNone(c.namespace_env("dev"))
        os.remove(os.path.join(self.sa, "token"))
        with self.assertRaises(kube.KubeError):
            self.client(Opener()).items("/api/v1/namespaces/dev/configmaps")

    def test_namespace_env(self):
        o = Opener(body={"metadata": {"labels": {"env": "prod"}}})
        self.assertEqual(self.client(o).namespace_env("prod"), "prod")
        self.assertEqual(o.requests[0].full_url, "https://k8s/api/v1/namespaces/prod")
        self.assertIsNone(self.client(Opener(body={"metadata": {}})).namespace_env("qa"))


if __name__ == "__main__":
    unittest.main()
