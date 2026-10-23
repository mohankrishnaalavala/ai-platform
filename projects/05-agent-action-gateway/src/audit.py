"""The audit log. Records are only ever added, one JSON object each:
- an intent record, written just before a change (request, decision + reasons + policy version, dry-run
  diff, before snapshot, approver). If it can't be written, the change doesn't happen.
- a result record, written after every write call, the denied and rejected ones too: the intent fields
  plus the result, the after snapshot and the undo token. undo_action reads this one.

What the records hold: the agent's arguments, including whole ConfigMap and Deployment manifests, and the
full before/after objects of the ConfigMaps and Deployments a call targets. Never a Secret: the gateway's
RBAC can't read one, and a manifest of any other kind is recorded by kind and name only (actions.recorded).
Never an agent token: only the team it maps to.

- Cloud (AUDIT_BUCKET set): objects <action_id>.intent.json and <action_id>.result.json in the GCS (Google
  Cloud Storage) audit bucket. The token comes from the GKE metadata server (Workload Identity), so there
  is no key. The bucket grants create and read only, and ifGenerationMatch=0 makes every write a create.
- kind (AUDIT_FILE): one line per record in a local file (JSON Lines)."""
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}")  # action ids, e.g. 20261021T153000Z-1a2b3c4d; use fullmatch
TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
GCS = "https://storage.googleapis.com"


class FileAudit:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def write(self, record):
        line = json.dumps(record, sort_keys=True) + "\n"
        with self.lock, open(self.path, "a") as f:
            f.write(line)

    def read(self, action_id):
        """The action's result record, or None."""
        try:
            with open(self.path) as f:
                for line in f:
                    record = json.loads(line)
                    if record.get("action_id") == action_id and record.get("record") == "result":
                        return record
        except FileNotFoundError:
            pass
        return None


class GcsAudit:
    def __init__(self, bucket, urlopen=urllib.request.urlopen):
        self.bucket = bucket
        self.urlopen = urlopen

    def _call(self, url, data=None):
        req = urllib.request.Request(TOKEN_URL, headers={"Metadata-Flavor": "Google"})
        with self.urlopen(req, timeout=5) as r:
            token = json.loads(r.read())["access_token"]
        req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {token}",
                                                              "Content-Type": "application/json"})
        with self.urlopen(req, timeout=15) as r:
            return r.read()

    def write(self, record):
        name = f"{record['action_id']}.{record['record']}.json"
        q = urllib.parse.urlencode({"uploadType": "media", "name": name, "ifGenerationMatch": "0"})
        self._call(f"{GCS}/upload/storage/v1/b/{self.bucket}/o?{q}", json.dumps(record, sort_keys=True).encode())

    def read(self, action_id):
        """The action's result record, or None."""
        try:
            return json.loads(self._call(f"{GCS}/storage/v1/b/{self.bucket}/o/{action_id}.result.json?alt=media"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
