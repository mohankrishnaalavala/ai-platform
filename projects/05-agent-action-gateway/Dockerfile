# The action gateway (P5): our MCP server. The base image is pinned by digest and every Python package by
# hash (requirements.txt). It runs as a non-root user; the pod sets a read-only root filesystem and mounts
# a writable /data (the local audit file and pending approvals in kind).
FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# shortcut: the 2026-11-05 base still ships zlib1g 1.3.1-1+b1 (CVE-2026-85091, HIGH, fixed in +deb13u1);
# drop this line once a newer base digest includes the fix.
RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade zlib1g && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
# pip goes afterwards: the server doesn't use it, and CI's image scan (trivy, job action-gateway) checks
# every package left in the image.
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && python -m pip uninstall -y -q pip && rm -rf /usr/local/lib/python3.13/ensurepip
COPY src/ /app/
WORKDIR /app
USER 65532:65532
CMD ["python", "server.py"]
