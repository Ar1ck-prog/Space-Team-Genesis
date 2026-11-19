"""Hand-written matcher/reward vectors (tests/fixtures/matcher_vectors.json), issue #4."""

import json
import unittest
from pathlib import Path

from auditgym.evaluator import Evaluator

DATA = json.loads((Path(__file__).resolve().parent / "fixtures" / "matcher_vectors.json").read_text(encoding="utf-8"))


def evaluator_for(vector: dict, mode: str = "open") -> Evaluator:
    issues = DATA["truth_T"] if vector["truth"] == "T" else vector["truth"]
    return Evaluator(DATA["episode_id"], {"episode_id": DATA["episode_id"], "issues": issues}, DATA["currencies"], mode)


class TestVectors(unittest.TestCase):
    def test_vector_names_unique(self):
        names = [v["name"] for v in DATA["vectors"]]
        self.assertEqual(len(names), len(set(names)))

    def test_all_vectors(self):
        for v in DATA["vectors"]:
            with self.subTest(v["name"]):
                ev = evaluator_for(v)
                res = ev.score_submission(v["submission"], v["inspection_steps"])
                e = v["expect"]
                self.assertEqual(res.valid, e["valid"], res.error)
                self.assertEqual((res.tp, res.fp, res.fn), (e["tp"], e["fp"], e["fn"]))
                self.assertAlmostEqual(res.f1, e["f1"], places=12)
                self.assertAlmostEqual(res.reward, e["reward"], places=12)
                self.assertAlmostEqual(ev.public_reward(), e["reward"], places=12)

    def test_closed_mode_public_reward_is_always_zero(self):
        for v in DATA["vectors"]:
            with self.subTest(v["name"]):
                ev = evaluator_for(v, "closed")
                res = ev.score_submission(v["submission"], v["inspection_steps"])
                self.assertEqual(ev.public_reward(), 0.0)
                self.assertAlmostEqual(res.reward, v["expect"]["reward"], places=12)  # kept internally

    def test_required_behaviours_hold_in_vectors(self):
        r = {v["name"]: v["expect"]["reward"] for v in DATA["vectors"]}
        self.assertGreater(r["all_correct"], r["silence_on_dirty"])  # correct beats silence on dirty books
        self.assertGreater(r["silence_on_clean"], r["accusation_on_clean"])  # silence beats false accusations
        self.assertLess(r["duplicate_finding"], r["all_correct"])  # duplicates give no gain
        self.assertLess(r["all_correct_zero_steps"] - r["all_correct_99_steps"], 0.25)  # step cost is small
        self.assertGreater(r["all_correct_99_steps"], r["two_of_three"])  # correctness dominates step cost


if __name__ == "__main__":
    unittest.main()
