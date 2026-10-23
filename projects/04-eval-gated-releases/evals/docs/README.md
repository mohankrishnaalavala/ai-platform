# Kubernetes docs pages (source for the Q&A cases)

The Q&A cases in [`../datasets/v1/qa.jsonl`](../datasets/v1/qa.jsonl) ask questions over excerpts of these 12 pages.

- **Source:** the Kubernetes documentation, [`kubernetes/website`](https://github.com/kubernetes/website), commit [`bc7f3479bc8b0af48097b155c22c8d58e8e1afe4`](https://github.com/kubernetes/website/tree/bc7f3479bc8b0af48097b155c22c8d58e8e1afe4).
- **Authors:** The Kubernetes Authors.
- **Licence:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). The full text is in [`LICENSE`](LICENSE), copied from the same commit.
- **Unmodified:** each file is byte-for-byte the file at that commit (same git blob hash).

| File | Page | Path at the commit |
|---|---|---|
| `configmap.md` | ConfigMaps | `content/en/docs/concepts/configuration/configmap.md` |
| `cron-jobs.md` | CronJob | `content/en/docs/concepts/workloads/controllers/cron-jobs.md` |
| `daemonset.md` | DaemonSet | `content/en/docs/concepts/workloads/controllers/daemonset.md` |
| `deployment.md` | Deployments | `content/en/docs/concepts/workloads/controllers/deployment.md` |
| `disruptions.md` | Disruptions | `content/en/docs/concepts/workloads/pods/disruptions.md` |
| `horizontal-pod-autoscale.md` | Horizontal Pod Autoscaling | `content/en/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale.md` |
| `job.md` | Jobs | `content/en/docs/concepts/workloads/controllers/job.md` |
| `pod-lifecycle.md` | Pod Lifecycle | `content/en/docs/concepts/workloads/pods/pod-lifecycle.md` |
| `replicaset.md` | ReplicaSet | `content/en/docs/concepts/workloads/controllers/replicaset.md` |
| `sidecar-containers.md` | Sidecar Containers | `content/en/docs/concepts/workloads/pods/sidecar-containers.md` |
| `statefulset.md` | StatefulSets | `content/en/docs/concepts/workloads/controllers/statefulset.md` |
| `ttlafterfinished.md` | Automatic Cleanup for Finished Jobs | `content/en/docs/concepts/workloads/controllers/ttlafterfinished.md` |

The prompts quote whole-line ranges of these pages, with Hugo shortcodes and link targets removed and wrapped lines joined. [`../datasets/v1/README.md`](../datasets/v1/README.md) says so too.
