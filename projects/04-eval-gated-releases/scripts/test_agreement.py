"""P4-04: the agreement sample is fixed and blind, and a sheet scored like the judge agrees 100%. Standard library only."""
import re
import unittest

import agreement


class Agreement(unittest.TestCase):
    def test_sample_is_fixed_blind_and_covers_20_questions(self):
        items = agreement.sample()
        self.assertEqual([c["id"] for _, c, _ in items], [c["id"] for _, c, _ in agreement.sample()])
        self.assertEqual(len({c["id"] for _, c, _ in items}), 20)
        for source in agreement.SOURCES:
            self.assertEqual(sum(s == source for s, _, _ in items), agreement.PER_SOURCE)
        sheet = agreement.sheet()
        self.assertNotIn("self-hosted", sheet)
        self.assertNotIn("gemini", sheet.lower())
        self.assertEqual(sheet.count("\nSCORE: \n"), 20)

    def test_scoring_like_the_judge_agrees_fully(self):
        scores = iter(c["score"] for _, c, _ in agreement.sample())
        filled = re.sub(r"(?m)^SCORE: $", lambda _: f"SCORE: {next(scores)}", agreement.sheet())
        self.assertIn("| Same score (exact agreement) | 20 of 20 (100%) |", agreement.score(filled))

    def test_a_missing_score_fails(self):
        with self.assertRaises(ValueError):
            agreement.score(agreement.sheet())


if __name__ == "__main__":
    unittest.main()
