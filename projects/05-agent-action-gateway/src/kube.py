"""A small Kubernetes API client, standard library only. It signs in with the pod's own ServiceAccount
token, so every call, dry runs included, has exactly that account's RBAC (role-based access control)
permissions (rbac.yaml) and no more."""
import http.client
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
PATHS = {"Deployment": "/apis/apps/v1/namespaces/{ns}/deployments",
         "ConfigMap": "/api/v1/namespaces/{ns}/configmaps"}
PATCH_TYPES = {"merge": "application/merge-patch+json", "json": "application/json-patch+json",
               "apply": "application/apply-patch+yaml"}  # server-side apply; JSON is valid YAML
FIELD_MANAGER = "action-gateway"


class KubeError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Kube:
    def __init__(self, base=None, sa_dir=SA_DIR, opener=None):
        self.base = base or f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ['KUBERNETES_SERVICE_PORT']}"
        self.sa_dir = sa_dir
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=f"{sa_dir}/ca.crt")))

    def call(self, method, path, body=None, ctype="application/json", **params):
        """The API server's answer. Any failure, the network's included, is a KubeError, so callers fail closed."""
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            with open(f"{self.sa_dir}/token") as f:  # read every time: Kubernetes rotates the mounted token
                token = f.read().strip()
            req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Authorization": f"Bearer {token}", "Content-Type": ctype,
                                                  "Accept": "application/json"})
            with self.opener.open(req, timeout=15) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read()).get("message") or e.reason
            except ValueError:
                message = e.reason
            raise KubeError(e.code, message) from None
        except (OSError, http.client.HTTPException, ValueError) as e:  # refused, timed out, TLS, a garbled answer
            raise KubeError(0, f"{type(e).__name__}: {e}") from None

    def get(self, kind, ns, name):
        """The object, or None if it doesn't exist."""
        try:
            return self.call("GET", f"{PATHS[kind].format(ns=ns)}/{name}")
        except KubeError as e:
            if e.status == 404:
                return None
            raise

    def items(self, path):
        return self.call("GET", path).get("items", [])

    def namespace_env(self, ns):
        """The namespace's `env` label, or None if it has none or can't be read (RBAC allows only dev,
        staging and prod)."""
        try:
            return (self.call("GET", f"/api/v1/namespaces/{ns}")["metadata"].get("labels") or {}).get("env")
        except KubeError:
            return None

    def send(self, kind, ns, name, change, dry_run):
        """Make a change ({"type": merge|json|apply|delete, "body": ...}), or with dry_run only ask the API
        server what it would do. Returns the object after the change (None after a delete)."""
        path = f"{PATHS[kind].format(ns=ns)}/{name}"
        if change["type"] == "delete":
            # dryRun goes in the DeleteOptions body: with a body, the API server ignores the query string
            self.call("DELETE", path, change["body"] | ({"dryRun": ["All"]} if dry_run else {}))
            return None
        params = {"dryRun": "All"} if dry_run else {}
        params["fieldManager"] = FIELD_MANAGER
        if change["type"] == "apply":
            params["force"] = "true"  # take over fields another manager set, as kubectl apply --force-conflicts
        return self.call("PATCH", path, change["body"], PATCH_TYPES[change["type"]], **params)
