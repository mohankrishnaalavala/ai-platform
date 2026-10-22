"""P4-01: the golden set's shape (evals/datasets/v1, defined in its README.md).

Checks every line's schema, unique ids, the exact counts, that every JSON Schema is valid, and that all personal
data is reserved fake data. Standard library only, so CI runs it with nothing installed.
"""
import json
import re
import unittest
from collections import Counter
from pathlib import Path

V1 = Path(__file__).resolve().parent.parent / "evals" / "datasets" / "v1"
COUNTS = {"qa": 40, "structured": 20, "instruction": 20, "safety": 20}
SAFETY_COUNTS = {"pii": 10, "refuse": 10}  # the 10 refuse-* cases are written by the user
ID_PREFIXES = {"qa": {"qa"}, "structured": {"structured"}, "instruction": {"instruction"}, "safety": {"pii", "refuse"}}

# Check kinds: required fields, optional fields.
KINDS = {
    "judge": ({"reference", "key_facts"}, set()),
    "json_schema": ({"schema"}, set()),
    "max_words": ({"value"}, set()),
    "min_words": ({"value"}, set()),
    "regex": ({"pattern"}, {"flags"}),
    "must_contain": ({"values"}, set()),
    "must_not_contain": ({"values"}, set()),
    "language": ({"value"}, set()),
    "bullets": ({"count"}, set()),
    "case": ({"value"}, set()),
    "order": ({"values"}, set()),
    "all": ({"checks"}, set()),
    "refuse": (set(), {"must_not_contain"}),
}
DETERMINISTIC = set(KINDS) - {"judge", "json_schema", "refuse"}
ALLOWED_KINDS = {"qa": {"judge"}, "structured": {"json_schema"}, "instruction": DETERMINISTIC,
                 "pii": {"must_not_contain"}, "refuse": {"refuse"}}

SCHEMA_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "pattern",
                   "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength", "description"}
SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}

# Reserved fake personal data: RFC 2606 domains, 555-0100 to 555-0199 (fictional use), the card networks'
# published test numbers, and placeholder names.
RESERVED_DOMAINS = {"example.com", "example.org", "example.net"}
TEST_CARDS = {"4111111111111111", "4012888888881881", "5555555555554444", "5500000000000004", "378282246310005",
              "371449635398431", "6011111111111117"}
FAKE_NAMES = {"Jane Doe", "John Doe", "Jane Roe", "Richard Roe", "Joe Bloggs", "Erika Mustermann", "Max Mustermann",
              "Testy McTestface", "Fakey Fakerson", "Sam Sampleton"}
NAME_VALUES = FAKE_NAMES | {part for name in FAKE_NAMES for part in name.split()}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
CARD = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?(\d{3})\)?[ .-]?(\d{3})[ .-]?(\d{4})(?!\d)")
SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")  # RFC 5737


def is_int(x, minimum):
    return type(x) is int and x >= minimum


def is_strings(x, min_len=1):
    return isinstance(x, list) and len(x) >= min_len and all(isinstance(v, str) and v.strip() for v in x)


def check_errors(check, allowed):
    """Problems with one check object; allowed is the set of kinds this case may use."""
    if not isinstance(check, dict) or check.get("type") not in KINDS:
        return [f"unknown check: {check!r}"]
    kind = check["type"]
    required, optional = KINDS[kind]
    errors = [] if kind in allowed else [f"check kind {kind} isn't allowed here"]
    keys = set(check) - {"type"}
    if not required <= keys or not keys <= required | optional:
        return errors + [f"{kind}: fields {sorted(keys)}, needs {sorted(required)} (optional {sorted(optional)})"]
    if kind == "judge":
        if not (isinstance(check["reference"], str) and check["reference"].strip()) or not is_strings(check["key_facts"]):
            errors.append("judge: needs a reference and at least one key fact")
    elif kind == "json_schema":
        if not isinstance(check["schema"], dict):
            errors.append("json_schema: schema must be an object")
    elif kind in ("max_words", "min_words"):
        errors += [] if is_int(check["value"], 1) else [f"{kind}: value must be a positive integer"]
    elif kind == "bullets":
        errors += [] if is_int(check["count"], 1) else ["bullets: count must be a positive integer"]
    elif kind == "regex":
        try:
            re.compile(check["pattern"])
        except (re.error, TypeError) as e:
            errors.append(f"regex: {e}")
        if not set(check.get("flags", "")) <= set("ims"):
            errors.append("regex: flags may only use i, m and s")
    elif kind in ("must_contain", "must_not_contain"):
        errors += [] if is_strings(check["values"]) else [f"{kind}: values must be non-empty strings"]
    elif kind == "language":
        errors += [] if re.fullmatch(r"[a-z]{2}", str(check["value"])) else ["language: value is an ISO 639-1 code"]
    elif kind == "case":
        errors += [] if check["value"] in ("lower", "upper") else ["case: value is lower or upper"]
    elif kind == "order":
        errors += [] if is_strings(check["values"], 2) else ["order: needs at least two values"]
    elif kind == "all":
        if not isinstance(check["checks"], list) or len(check["checks"]) < 2:
            errors.append("all: needs at least two checks")
        else:
            for sub in check["checks"]:
                errors += check_errors(sub, allowed & DETERMINISTIC - {"all"})
    elif kind == "refuse" and "must_not_contain" in check and not is_strings(check["must_not_contain"]):
        errors.append("refuse: must_not_contain must be non-empty strings")
    return errors


def schema_errors(s, path="schema"):
    """Structural check of the JSON Schema subset the golden set uses (draft 2020-12 keywords)."""
    if not isinstance(s, dict):
        return [f"{path}: not an object"]
    errors = [f"{path}: unknown keyword {k}" for k in sorted(set(s) - SCHEMA_KEYWORDS)]
    if not {"type", "enum", "const"} & set(s):
        errors.append(f"{path}: needs type, enum or const")
    t = s.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not types or not set(types) <= SCHEMA_TYPES or len(set(types)) != len(types):
            errors.append(f"{path}: bad type {t!r}")
    if "enum" in s:
        e = s["enum"]
        if not isinstance(e, list) or not e or len({json.dumps(v) for v in e}) != len(e):
            errors.append(f"{path}: enum must be a non-empty list of unique values")
    props = s.get("properties", {})
    if not isinstance(props, dict):
        errors.append(f"{path}: properties must be an object")
        props = {}
    for name, sub in props.items():
        errors += schema_errors(sub, f"{path}.{name}")
    if "required" in s:
        r = s["required"]
        if not is_strings(r, 0) or len(set(r)) != len(r) or not set(r) <= set(props):
            errors.append(f"{path}: required must list unique names from properties")
    if "additionalProperties" in s and not isinstance(s["additionalProperties"], bool):
        errors += schema_errors(s["additionalProperties"], f"{path}.additionalProperties")
    if "items" in s:
        errors += schema_errors(s["items"], f"{path}[]")
    if "pattern" in s:
        try:
            re.compile(s["pattern"])
        except (re.error, TypeError) as e:
            errors.append(f"{path}: pattern: {e}")
    for lo, hi in (("minimum", "maximum"), ("minItems", "maxItems"), ("minLength", "maxLength")):
        bounds = [s[k] for k in (lo, hi) if k in s]
        counts = lo != "minimum"
        if any(isinstance(v, bool) or not isinstance(v, int if counts else (int, float)) or (counts and v < 0) for v in bounds):
            errors.append(f"{path}: {lo}/{hi} must be {'non-negative integers' if counts else 'numbers'}")
        elif len(bounds) == 2 and bounds[0] > bounds[1]:
            errors.append(f"{path}: {lo} is above {hi}")
    return errors


def strings(x):
    """Every string inside a case: prompt, system and the check's values."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def luhn(digits):
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def reserved_phone(m):
    return m.group(2) == "555" and m.group(3).startswith("01")


def personal_data_errors(text):
    """Personal data in text that isn't reserved fake data."""
    errors = [f"email {m.group(0)}" for m in EMAIL.finditer(text) if m.group(1).lower() not in RESERVED_DOMAINS]
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if luhn(digits) and digits not in TEST_CARDS:
            errors.append(f"card-like number {m.group(0)}")
    rest = CARD.sub(" ", text)  # card runs aren't phone numbers
    errors += [f"phone {m.group(0)}" for m in PHONE.finditer(rest) if not reserved_phone(m)]
    errors += [f"SSN-like number {m.group(0)}" for m in SSN.finditer(text)]
    errors += [f"IP address {m.group(0)}" for m in IPV4.finditer(text) if not m.group(0).startswith(DOC_NETS)]
    return errors


def is_reserved_value(v):
    if v in NAME_VALUES:
        return True
    if EMAIL.fullmatch(v):
        return v.split("@")[1].lower() in RESERVED_DOMAINS
    if re.sub(r"\D", "", v) in TEST_CARDS:
        return True
    m = PHONE.fullmatch(v)
    return bool(m) and reserved_phone(m)


class GoldenSetV1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = {}  # file name -> [(line number, raw line)]
        cls.cases = {}  # file name -> [case]; lines that aren't JSON objects are left out (the schema test fails)
        for name in COUNTS:
            raw = (V1 / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            cls.lines[name] = list(enumerate(raw, 1))
            cls.cases[name] = []
            for _, line in cls.lines[name]:
                try:
                    case = json.loads(line)
                except ValueError:
                    continue
                if isinstance(case, dict):
                    cls.cases[name].append(case)

    def all_cases(self):
        return [c for cases in self.cases.values() for c in cases]

    def test_every_line_is_a_valid_case(self):
        for name, lines in self.lines.items():
            for n, line in lines:
                where = f"{name}.jsonl line {n}"
                try:
                    case = json.loads(line)
                except ValueError as e:
                    self.fail(f"{where}: not JSON ({e})")
                self.assertIsInstance(case, dict, where)
                keys = set(case)
                self.assertTrue({"id", "category", "prompt", "check"} <= keys <= {"id", "category", "prompt", "system", "check"},
                                f"{where}: fields {sorted(keys)}")
                m = re.fullmatch(r"([a-z]+)-(\d{3})", str(case["id"]))
                self.assertTrue(m and m.group(1) in ID_PREFIXES[name], f"{where}: id {case['id']!r}")
                self.assertEqual(case["category"], name, f"{where}: category")
                self.assertTrue(isinstance(case["prompt"], str) and case["prompt"].strip(), f"{where}: empty prompt")
                if "system" in case:
                    self.assertTrue(isinstance(case["system"], str) and case["system"].strip(), f"{where}: empty system")
                self.assertEqual(check_errors(case["check"], ALLOWED_KINDS[m.group(1)]), [], where)

    def test_ids_are_unique(self):
        dupes = [i for i, n in Counter(c.get("id") for c in self.all_cases()).items() if n > 1]
        self.assertEqual(dupes, [])

    def test_counts(self):
        self.assertEqual({name: len(lines) for name, lines in self.lines.items()}, COUNTS,
                         "cases per file; safety.jsonl needs 10 pii-* and 10 refuse-* (the user writes the refuse-* cases)")
        safety = Counter(str(c.get("id", "")).split("-")[0] for c in self.cases["safety"])
        self.assertEqual(dict(safety), SAFETY_COUNTS)

    def test_every_json_schema_is_valid(self):
        for case in self.all_cases():
            check = case.get("check", {})
            if check.get("type") != "json_schema":
                continue
            schema = check["schema"]
            self.assertEqual(schema_errors(schema), [], case["id"])
            self.assertEqual(schema.get("type"), "object", f"{case['id']}: the answer must be a JSON object")
            # The model must see the schema it is scored against: the prompt carries it on one line.
            shown = []
            for line in case["prompt"].splitlines():
                try:
                    shown.append(json.loads(line))
                except ValueError:
                    pass
            self.assertIn(schema, shown, f"{case['id']}: the prompt doesn't show the check's schema")

    def test_personal_data_is_reserved_fake_data(self):
        for case in self.all_cases():
            for text in strings(case):
                self.assertEqual(personal_data_errors(text), [], case.get("id"))

    def test_pii_checks_name_only_fake_values_from_their_prompt(self):
        pii = [c for c in self.cases["safety"] if str(c.get("id")).startswith("pii-")]
        for case in pii:
            values = case["check"]["values"]
            self.assertEqual([v for v in values if not is_reserved_value(v)], [], f"{case['id']}: not reserved fake data")
            prompt = case["prompt"].lower()
            self.assertEqual([v for v in values if v.lower() not in prompt], [], f"{case['id']}: values not in the prompt")


if __name__ == "__main__":
    unittest.main()
