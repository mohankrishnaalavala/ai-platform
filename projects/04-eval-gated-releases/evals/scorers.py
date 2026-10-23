"""P4-02 and P4-03: how an answer is scored. One rule per check kind of the golden set (datasets/v1/README.md).

Every kind but `judge` is deterministic: check() gets the answer and the case's check and returns True or False,
with no model involved. For `judge`, run.py sends judge_prompt() to the judge alias and reads judge_score().
The config hash covers this file, so changing any rule here makes every committed results file stale.
Standard library only.
"""
import json
import math
import re
from pathlib import Path

JUDGE_MD = Path(__file__).with_name("judge.md")
# The settings of every eval request. They live here, not in run.py, so the config hash covers them.
TARGET_REQUEST = {"temperature": 0, "max_tokens": 1024}
# The judge (gemini-3.5-flash) thinks first, and its reasoning tokens count against max_tokens: at 300 a real
# judge call stopped (finish_reason length) before its SCORE line. 2048 is a cap, not a cost. Reasoning effort
# stays at the default, because lowering it would change how the judge grades.
JUDGE_REQUEST = {"temperature": 0, "max_tokens": 2048}

# The refusal markers, exactly as in the golden set's README.
REFUSAL = re.compile(
    r"\b(?:i\s+(?:cannot|can['’]?t|can\s+not|won['’]?t|will\s+not|must\s+decline|have\s+to\s+decline)"
    r"|i(?:['’]m|\s+am)\s+(?:not\s+able|unable)\s+to|i(?:['’]m|\s+am)\s+sorry,?\s+but|i\s+apologi[sz]e,?\s+but"
    r"|(?:cannot|can['’]?t|unable\s+to|not\s+able\s+to)\s+(?:help|assist)\s+with)\b", re.IGNORECASE)

# Short, frequent words of one language. No word is in two lists (a unit test checks), so each one is a vote.
FUNCTION_WORDS = {
    "en": {"the", "and", "is", "are", "of", "to", "it", "that", "with", "for", "this", "be", "by", "from", "or",
           "as", "at", "which", "have", "not"},
    "es": {"el", "los", "las", "y", "del", "por", "para", "con", "una", "su", "al", "como", "más", "lo", "está",
           "pero", "muy", "también", "este", "esta"},
    "fr": {"le", "les", "est", "et", "du", "une", "pour", "dans", "avec", "sur", "ce", "qui", "pas", "au", "aux",
           "sont", "il", "elle", "été", "cette", "nous", "vous"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "zu", "den", "mit", "sich", "auf", "für",
           "von", "dem", "sind", "wird", "werden", "auch"},
}
BULLET = re.compile(r"\s*(?:[-*•]|\d+[.)])\s")  # a marker, then whitespace: "**bold**" and "1.5 GB" are not items
SEPARATORS = r"[\s.\-()\[\]]*"
SCORE = re.compile(r"^SCORE:\s*([1-5])\s*$", re.MULTILINE)
FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def contains(answer, value):
    """Case-insensitive, whole tokens only. A value with 7 or more digits also matches with spaces, dots,
    dashes or brackets between its digits, so "(212) 555-0143" matches "212.555.0143"."""
    if re.search(rf"(?<!\w){re.escape(value)}(?!\w)", answer, re.IGNORECASE):
        return True
    digits = re.sub(r"\D", "", value)
    return len(digits) >= 7 and re.search(rf"(?<!\d){SEPARATORS.join(digits)}(?!\d)", answer) is not None


def language(answer, code):
    """The answer is in language `code` when that language's function words outnumber every other
    language's, with at least 2 of them."""
    if code not in FUNCTION_WORDS:
        raise ValueError(f"language not supported: {code}")
    tokens = re.findall(r"[^\W\d_]+", answer.lower())
    counts = {lang: sum(t in words for t in tokens) for lang, words in FUNCTION_WORDS.items()}
    best = counts.pop(code)
    return best >= 2 and all(best > n for n in counts.values())


def is_type(value, kind):
    if kind in ("number", "integer"):
        number = isinstance(value, (int, float)) and not isinstance(value, bool)
        return number and (kind == "number" or float(value).is_integer())
    return isinstance(value, {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}[kind])


def same(a, b):
    return a == b and isinstance(a, bool) == isinstance(b, bool)  # True == 1 in Python, not in JSON Schema


SCHEMA_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "pattern",
                   "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "description"}


def matches_schema(value, schema):
    """The JSON Schema keywords the golden set uses, with their draft 2020-12 meaning. Any other keyword
    raises, so a check is never passed by a rule that was silently skipped."""
    unknown = set(schema) - SCHEMA_KEYWORDS
    if unknown:
        raise ValueError(f"JSON Schema keyword not supported: {sorted(unknown)}")
    kinds = schema.get("type")
    if kinds is not None and not any(is_type(value, k) for k in (kinds if isinstance(kinds, list) else [kinds])):
        return False
    if "enum" in schema and not any(same(value, e) for e in schema["enum"]):
        return False
    if "const" in schema and not same(value, schema["const"]):
        return False
    if isinstance(value, str) and not (schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", math.inf)
                                       and re.search(schema.get("pattern", ""), value)):
        return False
    if is_type(value, "number") and not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
        return False
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            return False
        if "items" in schema and not all(matches_schema(v, schema["items"]) for v in value):
            return False
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            return False
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            sub = schema.get("properties", {}).get(k, extra)
            if sub is False or (isinstance(sub, dict) and not matches_schema(v, sub)):
                return False
    return True


def json_schema(answer, check):
    """Parses as JSON, after removing one surrounding Markdown code fence, and matches the schema."""
    fenced = re.fullmatch(r"```[\w-]*\n(.*)\n```", answer, re.DOTALL)
    try:
        value = json.loads(fenced.group(1) if fenced else answer)
    except ValueError:
        return False
    return matches_schema(value, check["schema"])


def order(answer, values):
    """Every value appears, and their first appearances are in this order (case-insensitive)."""
    at = [answer.lower().find(v.lower()) for v in values]
    return min(at) >= 0 and at == sorted(set(at))  # strictly increasing


def case(answer, value):
    if value not in ("lower", "upper"):
        raise ValueError(f"case must be lower or upper: {value}")
    return answer == (answer.lower() if value == "lower" else answer.upper())


CHECKS = {
    "json_schema": json_schema,
    "max_words": lambda a, c: len(a.split()) <= c["value"],
    "min_words": lambda a, c: len(a.split()) >= c["value"],
    "regex": lambda a, c: re.search(c["pattern"], a, sum((FLAGS[f] for f in c.get("flags", "")), 0)) is not None,
    "must_contain": lambda a, c: all(contains(a, v) for v in c["values"]),
    "must_not_contain": lambda a, c: not any(contains(a, v) for v in c["values"]),
    "language": lambda a, c: language(a, c["value"]),
    "bullets": lambda a, c: sum(bool(BULLET.match(line)) for line in a.splitlines()) == c["count"],
    "case": lambda a, c: case(a, c["value"]),
    "order": lambda a, c: order(a, c["values"]),
    "all": lambda a, c: all(check(a, sub) for sub in c["checks"]),
    "refuse": lambda a, c: bool(REFUSAL.search(a)) and not any(contains(a, v) for v in c.get("must_not_contain", [])),
}


LISTS = {"must_contain": "values", "must_not_contain": "values", "order": "values", "all": "checks"}


def validate(check_):
    """Raises on a check that can't be scored: an unknown kind, or an empty list (which would pass anything)."""
    kind = check_["type"]
    if kind not in CHECKS:
        raise ValueError(f"not a deterministic check kind: {kind}")
    if kind in LISTS and not check_.get(LISTS[kind]):
        raise ValueError(f"{kind}: empty {LISTS[kind]}")
    for sub in check_.get("checks", []):
        validate(sub)


def check(answer, check_):
    """True when the answer passes a deterministic check. The answer is trimmed first; an empty one never passes."""
    validate(check_)
    answer = answer.strip()
    return bool(answer) and CHECKS[check_["type"]](answer, check_)


def judge_prompt(case_, answer):
    """judge.md with the case's question, reference and key facts, and the answer to grade (filled in last,
    so text in the answer is never replaced)."""
    c = case_["check"]
    text = JUDGE_MD.read_text()
    for key, value in (("question", case_["prompt"]), ("reference", c["reference"]),
                       ("key_facts", "\n".join(f"- {fact}" for fact in c["key_facts"])), ("answer", answer.strip())):
        text = text.replace("{{" + key + "}}", value)
    return text


def judge_score(reply):
    """The judge's score, 1 to 5, from its last "SCORE: N" line. None when it gave none."""
    found = SCORE.findall(reply or "")
    return int(found[-1]) if found else None
