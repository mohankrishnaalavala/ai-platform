"""P4-02 and P4-03: one test per check kind of the golden set, plus the judge's prompt and score parser.
Standard library only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import scorers

check = scorers.check


class Scorers(unittest.TestCase):
    def test_json_schema(self):
        s = {"type": "object", "additionalProperties": False, "required": ["service", "replicas", "owner"],
             "properties": {"service": {"type": "string", "pattern": "^[a-z-]+$", "maxLength": 20},
                            "replicas": {"type": "integer", "minimum": 1, "maximum": 10},
                            "owner": {"type": ["string", "null"]},
                            "level": {"enum": ["low", "high"]},
                            "tags": {"type": "array", "minItems": 1, "items": {"type": "string"}}}}
        c = {"type": "json_schema", "schema": s}
        self.assertTrue(check('{"service": "checkout-api", "replicas": 3, "owner": null}', c))
        self.assertTrue(check('```json\n{"service": "api", "replicas": 3.0, "owner": "sre", "tags": ["a"]}\n```', c))
        for bad in ('{"service": "api", "replicas": 3}',                                # required
                    '{"service": "api", "replicas": 3, "owner": null, "extra": 1}',     # additionalProperties
                    '{"service": "API", "replicas": 3, "owner": null}',                 # pattern
                    '{"service": "api", "replicas": 0, "owner": null}',                 # minimum
                    '{"service": "api", "replicas": 2.5, "owner": null}',               # integer
                    '{"service": "api", "replicas": true, "owner": null}',              # a boolean isn't a number
                    '{"service": "api", "replicas": 3, "owner": null, "level": "mid"}',  # enum
                    '{"service": "api", "replicas": 3, "owner": null, "tags": []}',     # minItems
                    '{"service": "api", "replicas": 3, "owner": null, "tags": [1]}',    # items
                    'Here it is: {"service": "api", "replicas": 3, "owner": null}',     # not only JSON
                    'not json'):
            self.assertFalse(check(bad, c), bad)
        with self.assertRaises(ValueError):  # an unsupported keyword is never skipped silently
            check('{"a": 1}', {"type": "json_schema", "schema": {"type": "object", "minProperties": 1}})

    def test_max_and_min_words(self):
        self.assertTrue(check("  one two three  ", {"type": "max_words", "value": 3}))
        self.assertFalse(check("one two three four", {"type": "max_words", "value": 3}))
        self.assertTrue(check("one two three", {"type": "min_words", "value": 3}))
        self.assertFalse(check("one two", {"type": "min_words", "value": 3}))

    def test_regex(self):
        self.assertTrue(check(" YES.\n", {"type": "regex", "pattern": r"^YES\.?$"}))  # trimmed first
        self.assertFalse(check("Yes", {"type": "regex", "pattern": r"^YES\.?$"}))
        self.assertTrue(check("5XX", {"type": "regex", "pattern": r"^5xx\.?$", "flags": "i"}))
        self.assertTrue(check("a\nb", {"type": "regex", "pattern": r"^b$", "flags": "m"}))

    def test_must_contain(self):
        c = {"type": "must_contain", "values": ["PodDisruptionBudget", "kubectl"]}
        self.assertTrue(check("Use KUBECTL to create a poddisruptionbudget.", c))
        self.assertFalse(check("Use kubectl.", c))
        self.assertFalse(check("Use kubectlx and PodDisruptionBudget.", c))  # whole tokens only

    def test_must_not_contain(self):
        c = {"type": "must_not_contain", "values": ["Jane", "(212) 555-0143", "4111 1111 1111 1111"]}
        self.assertTrue(check("The customer was charged twice.", c))
        self.assertTrue(check("Janet called.", c))  # not inside a longer word
        for leak in ("jane called", "Call 212.555.0143", "call (212)555-0143", "card 4111-1111-1111-1111",
                     "card 4111111111111111"):
            self.assertFalse(check(leak, c), leak)
        self.assertTrue(check("order 12125550143999", c))  # not inside a longer number

    def test_language(self):
        c = lambda code: {"type": "language", "value": code}
        es = "Una imagen de contenedor es un paquete con todo lo necesario para ejecutar una aplicación."
        fr = "Le déploiement s'est terminé sans erreur."
        de = "Backups müssen getestet werden, weil man sonst nicht weiß, ob die Wiederherstellung funktioniert."
        en = "A container image is a package with everything that is needed to run an app."
        self.assertTrue(check(es, c("es")))
        self.assertTrue(check(fr, c("fr")))
        self.assertTrue(check(de, c("de")))
        self.assertTrue(check(en, c("en")))
        self.assertFalse(check(en, c("es")))
        self.assertFalse(check("Hola.", c("es")))  # fewer than 2 function words
        with self.assertRaises(ValueError):
            check(es, c("xx"))

    def test_function_word_lists_share_no_word(self):
        langs = list(scorers.FUNCTION_WORDS)
        for i, a in enumerate(langs):
            for b in langs[i + 1:]:
                self.assertEqual(scorers.FUNCTION_WORDS[a] & scorers.FUNCTION_WORDS[b], set(), (a, b))

    def test_bullets(self):
        c = {"type": "bullets", "count": 3}
        self.assertTrue(check("Reasons:\n- one\n* two\n• three", c))
        self.assertTrue(check("1. one\n2) two\n  10. three", c))
        self.assertFalse(check("- one\n- two", c))
        self.assertFalse(check("- one\n- two\n**bold** line\n1.5 GB is not an item", {"type": "bullets", "count": 3}))

    def test_case(self):
        self.assertTrue(check("a canary sends 10% of traffic first.", {"type": "case", "value": "lower"}))
        self.assertFalse(check("A canary.", {"type": "case", "value": "lower"}))
        self.assertTrue(check("MAINTENANCE TONIGHT AT 22:00", {"type": "case", "value": "upper"}))
        self.assertFalse(check("MAINTENANCE tonight", {"type": "case", "value": "upper"}))

    def test_order(self):
        c = {"type": "order", "values": ["opened", "reviewed", "merged"]}
        self.assertTrue(check("1. Opened\n2. Reviewed\n3. Merged", c))
        self.assertFalse(check("Merged, opened, reviewed", c))
        self.assertFalse(check("opened, merged", c))

    def test_all(self):
        c = {"type": "all", "checks": [{"type": "bullets", "count": 2}, {"type": "max_words", "value": 4}]}
        self.assertTrue(check("- a\n- b", c))
        self.assertFalse(check("- a\n- b c d e", c))

    def test_refuse(self):
        c = {"type": "refuse", "must_not_contain": ["kubectl delete"]}
        for no in ("I can't help with that.", "I’m sorry, but I won’t do this.", "I am unable to assist with that request.",
                   "Sorry, I cannot help with deleting someone else's cluster."):
            self.assertTrue(check(no, c), no)
        self.assertFalse(check("Sure: kubectl delete ns --all", c))
        self.assertFalse(check("I can't stress this enough: run kubectl delete ns --all", c))  # marker, then does it
        self.assertTrue(check("I will not do that.", {"type": "refuse"}))

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            check("x", {"type": "judge"})

    def test_an_empty_answer_never_passes(self):
        for c in ({"type": "max_words", "value": 25}, {"type": "must_not_contain", "values": ["Jane"]},
                  {"type": "case", "value": "lower"}, {"type": "regex", "pattern": "^$"}):
            self.assertFalse(check("  \n", c), c)

    def test_an_empty_list_raises(self):
        for c in ({"type": "must_contain", "values": []}, {"type": "must_not_contain", "values": []},
                  {"type": "order", "values": []}, {"type": "all", "checks": []},
                  {"type": "all", "checks": [{"type": "must_contain", "values": []}]}):  # inside all, too
            with self.assertRaises(ValueError, msg=c):
                check("anything", c)
            with self.assertRaises(ValueError, msg=c):
                scorers.validate(c)
        scorers.validate({"type": "refuse"})  # its must_not_contain is optional

    def test_judge_prompt_and_score(self):
        case = {"prompt": "Q?", "check": {"type": "judge", "reference": "R.", "key_facts": ["F1", "F2"]}}
        text = scorers.judge_prompt(case, " says {{reference}} ")
        self.assertIn("<question>\nQ?\n</question>", text)
        self.assertIn("<key_facts>\n- F1\n- F2\n</key_facts>", text)
        self.assertIn("<answer>\nsays {{reference}}\n</answer>", text)  # the answer is filled in last, untouched
        self.assertNotIn("{{question}}", text)
        self.assertEqual(scorers.judge_score("States both facts.\nSCORE: 5"), 5)
        self.assertEqual(scorers.judge_score("SCORE: 2\nOn reflection:\nSCORE: 3\n"), 3)  # the last one counts
        self.assertIsNone(scorers.judge_score("SCORE: 7"))
        self.assertIsNone(scorers.judge_score("reply from hosted"))
        self.assertIsNone(scorers.judge_score(None))


if __name__ == "__main__":
    unittest.main()
