"""The environment's submit validator (PR #10) and the evaluator's validator must agree.

If they disagree, a submission the environment accepts could be scored -1 by the evaluator
(or the reverse). Runs every matcher vector through the public AuditEnvironment API.
Known, documented difference: the environment rejects arrays that are not already sorted,
the evaluator sorts them (docs/MATCHER.md, open question).
"""

import json
import unittest
from pathlib import Path

from auditgym.submission import SubmissionError, validate_submission

try:
    from space_team_genesis.environment import AuditEnvironment, EpisodeData, EvaluationResult
except ImportError:  # src/ layout: run with PYTHONPATH=src
    AuditEnvironment = None

DATA = json.loads((Path(__file__).resolve().parent / "fixtures" / "matcher_vectors.json").read_text(encoding="utf-8"))
KNOWN_DIFFERENCES = {"affected_unsorted"}


class _Accept:
    def evaluate(self, findings, inspection_steps):
        return EvaluationResult(reward=0.5)


def env_accepts(submission) -> bool:
    ep = DATA["episode_id"]
    data = EpisodeData(
        episode_id=ep,
        manifest={"schema_version": "1.0.0", "episode_id": ep, "base_currency": "KZT",
                  "currency_decimals": {c: 2 for c in DATA["currencies"]}},
        policy_summary={},
        records={"ledger": [], "invoices": [], "payments": [], "fx": []},
        documents={},
    )
    env = AuditEnvironment(data=data, evaluation_mode="open", evaluator=_Accept())
    env.reset()
    result = env.step({"kind": "submit", "findings": submission})
    return result["observation"]["kind"] == "submission"


def evaluator_accepts(submission) -> bool:
    try:
        validate_submission(submission, DATA["episode_id"], DATA["currencies"])
        return True
    except SubmissionError:
        return False


@unittest.skipIf(AuditEnvironment is None, "space_team_genesis not importable (run with PYTHONPATH=src)")
class TestValidatorsAgree(unittest.TestCase):
    def test_all_vectors(self):
        for v in DATA["vectors"]:
            with self.subTest(v["name"]):
                env_ok, ev_ok = env_accepts(v["submission"]), evaluator_accepts(v["submission"])
                if v["name"] in KNOWN_DIFFERENCES:
                    self.assertEqual((env_ok, ev_ok), (False, True))
                else:
                    self.assertEqual(env_ok, ev_ok)
                    self.assertEqual(ev_ok, v["expect"]["valid"])


if __name__ == "__main__":
    unittest.main()
