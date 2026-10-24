# P4 judge agreement: the owner's scores against the judge's, 20 Q&A answers

Scored by the owner by hand on 2026-10-23 (US Central time; 2026-10-24 UTC), blind: the sheet showed no model name and no judge score. Computed with `scripts/agreement.py score labels.md` (commit 2ed60e3, PR #82).

- **Raw data:** [`labels.md`](labels.md), the sheet as the owner filled it.
- **Sample:** 4 Q&A answers from the first run of each of the 5 configs in PR #78, 20 different questions out of 40. A fixed seed (20261023) picked and shuffled them by question id only, never by score (`SOURCES` and `SEED` in `scripts/agreement.py`).
- **The judge's scores:** each case's `score` field in those 5 results files (all 5 at git commit 7bccc2803f01). The judge is the gateway alias `judge`, `vertex_ai/gemini-3.5-flash` at temperature 0, with the rubric in `evals/judge.md` (sha256 81971bccf2a60a43…, the same in all 5 files). The owner used the same rubric, printed at the top of the sheet.
- **Measures, fixed before scoring:** exact agreement (the same score), agreement within one point, and the signed mean of judge minus owner. Agreement per config isn't reported: 4 answers each is too few.

## Result

| Measure | Value |
|---|---|
| Same score (exact agreement) | 13 of 20 (65%) |
| Within one point | 17 of 20 (85%) |
| Mean of judge minus owner (signed: above 0, the judge is kinder) | -0.30 points |
| Owner's scores, 1 to 5 | 1: 0, 2: 0, 3: 0, 4: 3, 5: 17 |
| Judge's scores, 1 to 5 | 1: 0, 2: 2, 3: 1, 4: 1, 5: 16 |

Config `projects/02-llm-gateway/helm/gateway/values.yaml` is the hosted alias `chat-hosted`: Gemini 3.5 Flash-Lite.

| Answer | Case | Config | Owner | Judge | Judge minus owner |
|---|---|---|---|---|---|
| 1 | qa-029 | `projects/02-llm-gateway/helm/gateway/values.yaml` | 5 | 5 | +0 |
| 2 | qa-025 | `projects/02-llm-gateway/helm/gateway/values.yaml` | 5 | 5 | +0 |
| 3 | qa-018 | `config/models/qwen2.5-7b-fp8.yaml` | 4 | 5 | +1 |
| 4 | qa-015 | `config/models/qwen2.5-7b-fp8.yaml` | 5 | 5 | +0 |
| 5 | qa-021 | `config/models/qwen2.5-7b-fp8.yaml` | 5 | 5 | +0 |
| 6 | qa-004 | `config/models/qwen2.5-7b-fp8.yaml` | 5 | 5 | +0 |
| 7 | qa-032 | `config/models/qwen3-8b.yaml` | 5 | 5 | +0 |
| 8 | qa-011 | `config/models/qwen2.5-7b-bf16.yaml` | 4 | 5 | +1 |
| 9 | qa-037 | `config/models/qwen3-8b.yaml` | 5 | 5 | +0 |
| 10 | qa-005 | `config/models/qwen2.5-1.5b.yaml` | 5 | 4 | -1 |
| 11 | qa-020 | `config/models/qwen2.5-7b-bf16.yaml` | 5 | 3 | -2 |
| 12 | qa-031 | `config/models/qwen3-8b.yaml` | 5 | 5 | +0 |
| 13 | qa-034 | `config/models/qwen2.5-1.5b.yaml` | 5 | 5 | +0 |
| 14 | qa-008 | `config/models/qwen2.5-1.5b.yaml` | 5 | 5 | +0 |
| 15 | qa-014 | `config/models/qwen2.5-1.5b.yaml` | 5 | 2 | -3 |
| 16 | qa-001 | `projects/02-llm-gateway/helm/gateway/values.yaml` | 5 | 5 | +0 |
| 17 | qa-039 | `config/models/qwen3-8b.yaml` | 4 | 5 | +1 |
| 18 | qa-022 | `config/models/qwen2.5-7b-bf16.yaml` | 5 | 5 | +0 |
| 19 | qa-033 | `projects/02-llm-gateway/helm/gateway/values.yaml` | 5 | 5 | +0 |
| 20 | qa-006 | `config/models/qwen2.5-7b-bf16.yaml` | 5 | 2 | -3 |

## Where they disagree

The scores differ on 7 answers. The judge scored lower than the owner on 4 (answers 10, 11, 15 and 20, including both 3-point gaps and the one 2-point gap) and higher on 3. For the 4 lower scores, here is the judge's reason, unchanged from the results file, with the reference answer it graded against:

| Answer | Case | Owner | Judge | Reference answer (short) | The judge's reason |
|---|---|---|---|---|---|
| 15 | qa-014 | 5 | 2 | "No. Recreate only guarantees termination before creation for upgrades…" | "The answer incorrectly starts with "Yes" (contradicting its own explanation) and misses the key facts about the guarantee only applying to upgrades and the recommendation to use a StatefulSet." |
| 20 | qa-006 | 5 | 2 | "The run is skipped… future runs are still scheduled. Kubernetes treats a Job that misses its deadline as a failed Job…" | "…it misses the fact that future runs are still scheduled, and it incorrectly states how the missed run is treated (it is treated as failed, not as if the field were unset)." |
| 11 | qa-020 | 5 | 3 | "8 replicas… At 105m the ratio is 1.05, which is within the default tolerance of 0.1, so the control plane skips scaling and stays at 4." | "…misses the fact that no scaling occurs for the 105m scenario due to the default tolerance." |
| 10 | qa-005 | 5 | 4 | "…Forbid skips the new run…" | "…a minor imprecision regarding the "Forbid" policy, incorrectly suggesting the skipped run is merely delayed until the current run completes." |

On the other 3 (answers 3, 8 and 17), the judge gave 5 where the owner gave 4.

## Limits

- **Mostly 5s.** The owner gave 17 fives and the judge 16, so this sample says little about how the judge scores weak answers.
- **One labeller, 20 answers.** The numbers are a check, not a benchmark.
- **The judge is a Gemini model** grading answers that include Gemini Flash-Lite's (answers 1, 2, 16 and 19). Both scored all four 5, so self-preference can't be seen here either way.
- **The judge may be right on some gaps.** In qa-014 the answer begins "Yes" where the reference says "No". The owner's labels are reported as scored blind, not corrected afterwards.
- **Nothing was changed after scoring.** Neither the owner's labels nor the judge's rubric were edited to raise agreement.
