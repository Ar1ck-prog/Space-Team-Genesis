"""Committed public trajectories (case section 09): strict fields, no private data, reproducible."""

import json
import unittest
from pathlib import Path

from auditgym.analysis import TRAJECTORY_FIELDS, open_specs, trajectory_text

try:
    import space_team_genesis  # noqa: F401
except ImportError:  # src/ layout: run with PYTHONPATH=src
    space_team_genesis = None

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "trajectories" / "trajectories.jsonl"
PRIVATE = ("truth", "provenance", "expected_amount_minor", "evidence_sets", "issue_id", '"seed"', "clean_ledger",
           "matched", "precision")


@unittest.skipIf(space_team_genesis is None, "space_team_genesis not importable (run with PYTHONPATH=src)")
class TestCommittedTrajectories(unittest.TestCase):
    def test_files_exist(self):
        self.assertTrue(MAIN.is_file(), "run: python -m auditgym.analysis --trajectories trajectories")
        for key in ("random", "mark_all", "careful"):
            self.assertTrue((ROOT / "trajectories" / "by_policy" / f"{key}.jsonl").is_file())

    def test_strict_fields_and_lifecycle(self):
        for path in [MAIN, *sorted((ROOT / "trajectories" / "by_policy").glob("*.jsonl"))]:
            text = path.read_text(encoding="utf-8")
            for tok in PRIVATE:
                self.assertNotIn(tok, text, (path.name, tok))
            by_episode = {}
            for line in text.splitlines():
                row = json.loads(line)
                self.assertEqual(tuple(row), TRAJECTORY_FIELDS, path.name)
                self.assertTrue(-1 <= row["reward"] <= 1)
                by_episode.setdefault(row["episode_id"], []).append(row)
            for eid, rows in by_episode.items():
                self.assertEqual([r["step"] for r in rows], list(range(1, len(rows) + 1)), eid)
                self.assertTrue(rows[-1]["terminated"] or rows[-1]["truncated"], eid)
                self.assertFalse(any(r["terminated"] or r["truncated"] for r in rows[:-1]), eid)
                self.assertTrue(all(r["reward"] == 0 for r in rows[:-1]), eid)  # exploration earns nothing

    def test_main_file_covers_all_open_episodes(self):
        eids = {json.loads(l)["episode_id"] for l in MAIN.read_text(encoding="utf-8").splitlines()}
        self.assertEqual(eids, {e["episode_id"] for e in open_specs()})

    def test_regeneration_is_byte_identical(self):
        self.assertEqual(MAIN.read_bytes(), trajectory_text(open_specs(), "reconcile").encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
