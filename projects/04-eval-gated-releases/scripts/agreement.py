#!/usr/bin/env python3
"""P4-04: how often the judge agrees with the owner's own scores, on 20 Q&A answers.

  agreement.py sheet > labels.md      # the 20 answers to score by hand: no model name, no judge score
  agreement.py score labels.md        # after scoring: agreement as markdown

The sample is fixed: 4 answers from the first run of each of the 5 configs, 20 different questions, picked
and shuffled with SEED by question id only. So `score` rebuilds it, and no key file can leak the judge's
scores. Agreement is decided before labelling: exact (same score) and within one point, both reported.
Standard library only.
"""
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P4 = ROOT / "projects/04-eval-gated-releases"
SEED = 20261023
PER_SOURCE = 4
# The first of the two runs of each config (PR #78), frozen so later results can't change the sample.
SOURCES = [
    "results/f5299ab1903d4bfd/20261023t153526z-chat-default.json",  # Qwen2.5-7B BF16
    "results/717814b3d1ca1a4c/20261023t155323z-chat-default.json",  # Qwen2.5-7B FP8
    "results/2eabd020280bce87/20261023t161217z-chat-default.json",  # Qwen3-8B
    "results/4f75e5f4515af553/20261023t162716z-chat-default.json",  # Qwen2.5-1.5B
    "results/9c61a0b85593ff9b/20261023t163624z-chat-hosted.json",   # Gemini 3.5 Flash-Lite
]


def sample():
    """The 20 items in sheet order: (source, results case, golden-set case)."""
    golden = {c["id"]: c for c in map(json.loads, (P4 / "evals/datasets/v1/qa.jsonl").read_text().splitlines())}
    rng = random.Random(SEED)
    ids = sorted(golden)
    rng.shuffle(ids)
    items = []
    for n, source in enumerate(SOURCES):
        cases = {c["id"]: c for c in json.loads((P4 / source).read_text())["cases"] if c["category"] == "qa"}
        items += [(source, cases[i], golden[i]) for i in ids[n * PER_SOURCE:(n + 1) * PER_SOURCE]]
    rng.shuffle(items)
    return items


def quote(text):
    return "\n".join("> " + line if line else ">" for line in text.splitlines())


def sheet():
    judge = (P4 / "evals/judge.md").read_text()
    rubric = judge[judge.index("## Rubric"):judge.index("## Output")].strip()
    out = ["# Judge agreement: score 20 answers by hand (P4-04)", "",
           ("Score each answer from 1 to 5 with the judge's own rubric below, and write the number after its "
            "`SCORE:` line. The model and the judge's score are hidden on purpose: don't look them up until "
            "you're done. Then run `scripts/agreement.py score <this file>`."), "",
           rubric.replace("## ", "### "), ""]
    for n, (_, case, golden) in enumerate(sample(), 1):
        out += [f"## Answer {n}", "", "**Question, with its excerpt:**", "", quote(golden["prompt"]), "",
                f"**Reference answer:** {golden['check']['reference']}", "", "**Key facts:**", "",
                *[f"- {f}" for f in golden["check"]["key_facts"]], "", "**Answer to grade:**", "",
                quote(case["answer"]), "", "SCORE: ", ""]
    return "\n".join(out)


def labels(text):
    """{item number: score} from a filled sheet; every item needs a whole number from 1 to 5."""
    found, n = {}, None
    for line in text.splitlines():
        if m := re.match(r"## Answer (\d+)$", line):
            n = int(m.group(1))
        elif line.startswith("SCORE:"):
            m = re.fullmatch(r"SCORE:\s*([1-5])\s*", line)
            if not m:
                raise ValueError(f"answer {n}: write a whole number from 1 to 5 after SCORE:")
            found[n] = int(m.group(1))
    if sorted(found) != list(range(1, len(SOURCES) * PER_SOURCE + 1)):
        raise ValueError(f"expected scores for answers 1 to {len(SOURCES) * PER_SOURCE}, got {sorted(found)}")
    return found


def score(text):
    human = labels(text)
    rows, exact, near, diff = [], 0, 0, 0
    for n, (source, case, _) in enumerate(sample(), 1):
        config = json.loads((P4 / source).read_text())["config"]
        j, h = case["score"], human[n]
        exact, near, diff = exact + (j == h), near + (abs(j - h) <= 1), diff + (j - h)
        rows.append(f"| {n} | {case['id']} | `{config}` | {h} | {j} | {j - h:+d} |")
    total = len(rows)
    return "\n".join([
        "| Measure | Value |", "|---|---|",
        f"| Same score (exact agreement) | {exact} of {total} ({100 * exact / total:.0f}%) |",
        f"| Within one point | {near} of {total} ({100 * near / total:.0f}%) |",
        f"| Mean of judge minus owner | {diff / total:+.2f} points |", "",
        "| Answer | Case | Config | Owner | Judge | Judge minus owner |", "|---|---|---|---|---|---|", *rows])


if __name__ == "__main__":
    if sys.argv[1:] == ["sheet"]:
        print(sheet())
    elif len(sys.argv) == 3 and sys.argv[1] == "score":
        print(score(Path(sys.argv[2]).read_text()))
    else:
        sys.exit(__doc__)
