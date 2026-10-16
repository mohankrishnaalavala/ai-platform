#!/usr/bin/env python3
"""Upload files to Cloud Storage without ever overwriting: create-only access is all callers have.

Usage: upload.py gs://<bucket>/<prefix> <file>...
Token: GCS_TOKEN if set (the workflow: gcloud auth print-access-token), else the GKE metadata
server (a pod's Workload Identity). The gcloud CLI isn't used because it also needs read access.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"


def token():
    if os.environ.get("GCS_TOKEN"):
        return os.environ["GCS_TOKEN"]
    req = urllib.request.Request(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["access_token"]


def upload(path, bucket, name, tok):
    # ifGenerationMatch=0: create only; an existing object fails with 412 instead of being replaced.
    url = (f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
           f"?uploadType=media&ifGenerationMatch=0&name={urllib.parse.quote(name, safe='')}")
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {tok}", "Content-Type": "application/octet-stream"})
    urllib.request.urlopen(req, timeout=60).close()


def main(dest, paths):
    if not dest.startswith("gs://") or "/" not in dest[5:]:
        sys.exit("destination must be gs://<bucket>/<prefix>")
    bucket, prefix = dest[5:].split("/", 1)
    tok = token()
    for p in paths:
        upload(p, bucket, f"{prefix.rstrip('/')}/{os.path.basename(p)}", tok)
        print(f"uploaded {os.path.basename(p)}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])
