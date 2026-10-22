# The action gateway (P5): our MCP server. The base image is pinned by digest and every Python package by
# hash (requirements.txt). It runs as a non-root user; the pod sets a read-only root filesystem and mounts
# a writable /data (the local audit file and pending approvals in kind).
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
# pip goes afterwards: the server doesn't use it, and CI's image scan (trivy, job action-gateway) checks
# every package left in the image.
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && python -m pip uninstall -y -q pip && rm -rf /usr/local/lib/python3.13/ensurepip
COPY src/ /app/
WORKDIR /app
USER 65532:65532
CMD ["python", "server.py"]
