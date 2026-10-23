# Install the action gateway on your own machine (kind)

This guide puts the action gateway on a kind cluster (Kubernetes in Docker) on your laptop. Then an agent calls it, and you watch it allow, deny, ask for approval and undo. It needs no cloud account and no Slack:

- **Approvals** come from the CLI (command line) approver: you answer with `kubectl exec`.
- **The audit log** is a local file in the gateway's pod, not a cloud bucket.
- **The risk summary** says "unavailable": there is no LLM (large language model) gateway in kind, and approval works without it.

It's the same path CI (continuous integration) uses on every pull request (`tests/kind-smoke.sh`), one step at a time.

**Tested** on 2026-10-22 on macOS 26.5 (Intel, x86_64) with Docker 28.3.3. CI runs the same steps on Linux (Ubuntu 24.04, x86_64) through `tests/kind-smoke.sh`. Not tried: Apple silicon (arm64), Windows, and Claude Code as the agent (step 6).

## 1. What you need

| Tool | Version | Used for |
|---|---|---|
| Docker | Engine 28 or newer (tested 28.3.3) | Building the gateway's image; kind runs its node in Docker |
| kind | v0.33.0 | The cluster |
| kubectl | v1.36.4 | Everything you do in the cluster |
| Helm | v4.3.0 | Installing the gateway's chart |
| Python | 3.13 | The example agent (`call.py`) and, on macOS, the 50 scenarios |
| git, curl, openssl | any recent | Getting the code, tokens |

kind, kubectl and Helm at those versions, each checked against a fixed sha256. The values come from the checksum files each project publishes, and were checked against the downloads on 2026-10-22. This puts the tools in `~/.local/bin`; make sure that folder is on your `PATH`.

```bash
os=$(uname -s | tr '[:upper:]' '[:lower:]')                      # darwin or linux
arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')        # amd64 or arm64
case "$os-$arch" in
  darwin-amd64)
    kind_sha=5a99f26f57246dc9319dd294803313197a0f34d33c525b3ea8b655db5916ece0
    kubectl_sha=71a3aa7c2ee2c974d9fbb462cba0c5c04a4df2e8d85eee94714fd819ea3c4e63
    helm_sha=347a784877e0e20eac865e8d1c36a80f6bb0861d6f29abd34defb6570ef95d92 ;;
  darwin-arm64)
    kind_sha=0c8c7dbe5e23594a198b786c4bc13dacc101fa6196b0cb0b23a1ca44e61f4b4f
    kubectl_sha=c9e4f713d6fee0043a3d835cca13077cda2bc0973840eb9779360df0b5bdfc69
    helm_sha=d3870437e1e95b67f8edbde964156c84a26503f560821d40c542441658934fba ;;
  linux-amd64)
    kind_sha=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
    kubectl_sha=8b8f088da2dab964f853b38464033b1be15ede2839eca751482357c45abdd05a
    helm_sha=86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb ;;
  linux-arm64)
    kind_sha=20022bee6cfcd5086cb7234d218e3454e6090022f2a8f55d1fa7fcf42c3867a2
    kubectl_sha=0ecf44450ee6063bf19dd166a103ee6df4a9034455c2abce626e6eea657d73fb
    helm_sha=31c5794dd55c66a51e6b7d2e2ac7a114ae8b1de41ff1d9ba51748ac973b06a08 ;;
  *) echo "no checksums in this guide for $os-$arch" ;;
esac
mkdir -p ~/.local/bin && cd "$(mktemp -d)"
curl -fsSLo kind "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-$os-$arch"
curl -fsSLo kubectl "https://dl.k8s.io/release/v1.36.4/bin/$os/$arch/kubectl"
curl -fsSLo helm.tgz "https://get.helm.sh/helm-v4.3.0-$os-$arch.tar.gz"
printf '%s  kind\n%s  kubectl\n%s  helm.tgz\n' "$kind_sha" "$kubectl_sha" "$helm_sha" | shasum -a 256 -c
tar -xzf helm.tgz "$os-$arch/helm"
chmod +x kind kubectl && mv kind kubectl "$os-$arch/helm" ~/.local/bin/
cd - >/dev/null
```

The `shasum` line must print `OK` three times. If it prints `FAILED`, stop: don't move the files. On Linux, `sha256sum -c` works in place of `shasum -a 256 -c`.

## 2. Get the code and a Python environment

```bash
git clone https://github.com/mohankrishnaalavala/ai-platform.git
cd ai-platform/projects/05-agent-action-gateway
python3.13 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.txt
```

The packages are pinned by hash, the same ones the gateway's image has. Every command below runs from this folder.

## 3. Create the cluster and load the image

```bash
export KUBECONFIG="$HOME/.kube/action-gateway-kind"   # a kubeconfig of its own: your other clusters stay untouched
kind create cluster --name action-gateway \
  --image kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed
docker build -t action-gateway:ci .
kind load docker-image action-gateway:ci --name action-gateway
```

Run the same `export KUBECONFIG=...` line in every new terminal you open for this guide.

The tag `action-gateway:ci` is what the chart's kind settings expect (`tests/kind/values.yaml`). kind loads the image straight into its node, so no registry is needed.

## 4. Add the demo targets

```bash
python3 -c 'import json; print(json.dumps({"apiVersion": "v1", "kind": "List", "items": json.load(open("evals/base.yaml"))["objects"]}))' \
  | kubectl apply -f -
```

This makes the `dev`, `staging` and `prod` namespaces, each with an `env` label. Each one gets a `checkout` Deployment with 2 replicas, and a PodDisruptionBudget (PDB) that keeps at least 1 of them running. These are the objects the 50 scenarios start from.

The gateway works only in these three namespaces. Its RBAC (role-based access control) names them, and the policy reads each namespace's `env` label. A namespace without the label is denied.

## 5. Install the gateway with an agent token

```bash
kubectl apply -f helm/namespace.yaml
# Stands in for the cloud's secret store. There's no LLM gateway in kind, so the value is never used.
kubectl -n action-gateway create secret generic action-gateway-secrets --from-literal=action-gateway-team-key=unused-in-kind

export ACTION_GATEWAY_TOKEN=$(openssl rand -hex 32)
hash=$(printf '%s' "$ACTION_GATEWAY_TOKEN" | openssl dgst -sha256 | sed 's/^.*= //')
helm install action-gateway helm/action-gateway -n action-gateway -f tests/kind/values.yaml \
  --set-json agentTokens="{\"$hash\": \"payments\"}" \
  --set-file 'policies.gateway\.rego=policies/gateway.rego' --set-file 'policies.data\.json=policies/data.json' \
  --wait --timeout 3m
```

- **The token** is the agent's key. It maps to the team `payments`. It stays in your shell: only its SHA-256 hash (a one-way fingerprint) goes into the cluster, in the `agent-tokens` ConfigMap. Don't write the token to a file.
- **The policy** is `policies/gateway.rego` (rules) and `policies/data.json` (limits, freeze window). Change them and run `helm upgrade` with the same flags to try your own rules.
- **`tests/kind/values.yaml`** picks the CLI approver, the local audit file and the test Secret.

`kubectl -n action-gateway get pods` shows one pod with `2/2` containers: the gateway and its OPA (Open Policy Agent) sidecar.

## 6. Connect an agent

The gateway listens only inside its pod. Reach it with a port-forward, in a second terminal (with the same `KUBECONFIG`):

```bash
kubectl -n action-gateway port-forward deploy/action-gateway 8080:8080
```

If it stops at once with "connection refused", the server hadn't started yet. Wait a few seconds and run it again. Then, in the first terminal, a call without a token must be refused:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/mcp   # prints 401
```

**Claude Code (not tried in this guide's test).** Add this `.mcp.json` to the folder you start Claude Code from, and start it from the shell that has `ACTION_GATEWAY_TOKEN`. Claude Code fills in `${ACTION_GATEWAY_TOKEN}` from the environment, so the token isn't in the file.

```json
{
  "mcpServers": {
    "action-gateway": {
      "type": "http",
      "url": "http://127.0.0.1:8080/mcp",
      "headers": {"Authorization": "Bearer ${ACTION_GATEWAY_TOKEN}"},
      "timeout": 1200000
    }
  }
}
```

The `timeout` (20 minutes) is longer than the 15 minutes a call can wait for an approval. Then ask it, for example, "scale checkout in dev to 3 replicas" or "scale checkout in prod to 0".

**Any other MCP client.** The steps below use this small one, which makes one tool call per run, as an agent would. Save it as `call.py`:

```python
# One MCP tool call to the action gateway, as an agent makes it. Usage: call.py TOOL '{"argument": "value"}'
import json
import os
import sys

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main(tool, args):
    token = os.environ["ACTION_GATEWAY_TOKEN"]
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=1200)
    async with Client(streamable_http_client("http://127.0.0.1:8080/mcp", http_client=http)) as client:
        result = await client.call_tool(tool, json.loads(args))
    print(json.dumps(result.structured_content, indent=2))


anyio.run(main, sys.argv[1], sys.argv[2])
```

## 7. Try an allowed call and a denied one

**Allowed:** scale `checkout` in `dev` from 2 to 3 replicas.

```bash
.venv/bin/python call.py scale_deployment '{"namespace": "dev", "name": "checkout", "replicas": 3}'
```

The answer has `"decision": "allow"`, the reason `dev write within the limits`, the dry run (`pods_affected`, `capacity_change_pct`, `pdb_violations`), the diff, and `"result": {"status": "executed"}`. Note its `action_id` for step 8.

**Denied:** scale `checkout` in `prod` to 0.

```bash
.venv/bin/python call.py scale_deployment '{"namespace": "prod", "name": "checkout", "replicas": 0}'
```

The answer has `"decision": "deny"`, the reasons `scaling to 0 replicas in prod is never allowed` and `would violate PodDisruptionBudget checkout`, and `"status": "denied"`. Nothing changed: `kubectl -n prod get deploy checkout` still shows 2 replicas.

**Needs approval (optional):** any write in `prod`, for example 2 to 3 replicas. The call waits for a person, for up to 15 minutes:

```bash
.venv/bin/python call.py scale_deployment '{"namespace": "prod", "name": "checkout", "replicas": 3}'
```

While it waits, answer it from another terminal (with the same `KUBECONFIG`). `list` shows the request: reasons, dry run and diff. Then approve (or `reject`) it by its `action_id`:

```bash
kubectl -n action-gateway exec deploy/action-gateway -c gateway -- python approval.py list
kubectl -n action-gateway exec deploy/action-gateway -c gateway -- python approval.py approve <action_id> --by <your name>
```

The call then returns `"decision": "needs_approval"`, `"status": "executed"`, your name as the approver, and `"risk_summary": "unavailable"`. The CLI approver records whatever name `--by` gives; nothing proves who answered. That's why it's for kind only (the cluster uses Slack, which sends the user's ID itself).

**The clock is real here.** The weekend freeze (Friday 17:00 to Monday 08:00, America/Chicago) denies every `prod` write. During it, the prod examples are denied with `change freeze "weekend freeze" is active`.

## 8. Undo

Wait until the scale-up has rolled out, then undo it with the `action_id` from step 7:

```bash
kubectl -n dev rollout status deploy/checkout
.venv/bin/python call.py undo_action '{"action_id": "<action_id>"}'
kubectl -n dev get deploy checkout   # 2 replicas again
```

The undo goes through the same checks as any call (here: allowed, executed). Why the wait: straight after a scale-up, the new pods aren't ready yet, and the undo (a scale back down) is denied with `would violate PodDisruptionBudget`. It fails closed: nothing changes, and the undo works once the rollout is done.

Every call that writes leaves records in the audit file, with the request, decision, reasons, dry run, diff, before and after state, approver and result:

```bash
kubectl -n action-gateway exec deploy/action-gateway -c gateway -- tail -n 2 /data/audit.jsonl
```

The file lives as long as the pod: a restart empties it. The cluster install writes to a Cloud Storage bucket instead.

## 9. Run the 50 scenarios

`tests/kind-smoke.sh` makes its own throwaway kind cluster (`p5-smoke`), installs everything as above, and runs a smoke test and then all 50 scenarios (20 safe, 15 risky, 15 dangerous). It deletes that cluster at the end. It uses the `action-gateway:ci` image from step 3 and port 18080.

```bash
SMOKE_PYTHON=.venv/bin/python tests/kind-smoke.sh   # macOS
tests/kind-smoke.sh                                 # Linux
```

- **Why the difference:** on Linux, the script runs its agent inside the gateway's image, on Docker's host network. On macOS, Docker's host network can't reach the port-forward, so `SMOKE_PYTHON` names a local Python with `requirements.txt` installed: the `.venv` from step 2.
- **The output:** it takes a few minutes. Each scenario prints a line (`ok` or `FAIL`), then the metrics. It ends with `scenario results: <folder>/scenarios.md and scenarios.json`; set `RESULTS_DIR` to choose the folder.
- **What a pass means:** the script exits 0 only if every check of every scenario passed and no dangerous call was allowed.

## 10. Clean up

```bash
kind delete cluster --name action-gateway
rm -f "$KUBECONFIG" call.py
unset ACTION_GATEWAY_TOKEN KUBECONFIG
docker image rm action-gateway:ci   # optional
```

Stop the port-forward (Ctrl-C), and remove the `action-gateway` entry from `.mcp.json` if you added it. The token is now useless: its hash went with the cluster.

## Beyond kind (not tested)

On a real cluster, three things change (the session cluster's `deploy.sh` does all three):

- **The image** must come from a registry the cluster can pull from, pinned by digest (`image.ref: <registry>/action-gateway@sha256:...`). The chart refuses a tag unless `image.pullPolicy` is `Never`.
- **Approvals** should come from Slack ([`slack.md`](slack.md)). The CLI approver's name is self-declared.
- **The audit log** should go to a Cloud Storage bucket the gateway can create and read objects in, but never overwrite or delete (`audit.bucket`). The pod's local file is gone when the pod is.

The [threat model](threat-model.md) lists what the gateway does and doesn't protect against.
